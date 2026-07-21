# -*- coding: utf-8 -*-
"""Этапы 21+23 — монитор новизны прайса и накопительный трекер новых семейств.

После апдейта проверяет, не появились ли позиции, которые будут плохо искаться НАРОДНЫМ
языком. Такие позиции ищутся по прямому имени и штрихкоду сразу (жёсткие + лексический
каналы), но их народные синонимы («болгарка»→УШМ) резолвит словарь `slovar_svodnyy.json` —
а для НОВОГО семейства синонимов там ещё нет. Монитор ловит именно этот разрыв и сигналит,
что нужна ручная словарная правка (субагенты Haiku — см. заметку в корневом CLAUDE.md).

Что считается новым:
  - СЕМЕЙСТВО (первое слово имени), которого нет в словаре → нужен словарный вход с синонимами;
    базовый шум 0 (все текущие 706 семейств уже в словаре из 707 ключей).
  - ПОДГРУППА Форы, которой не было при прошлых прогонах (таблица `known_podgruppy`;
    первый прогон = посев без ложных срабатываний).

Этап 23 — трекер `novelty_families`: снимок (`NOVELTY.md`) истории не копил, поэтому семейства
накапливаются в таблице с датами (`first_seen`/`last_seen`, счётчик, пример, `status`). Это
рабочий список «по чему писать словарь». **Авто-закрытие**: как только семейство появилось в
`slovar_svodnyy.json`, следующий прогон помечает его `внесено` — без ручной пометки «сделано».

Отчёт — `docs/NOVELTY.md` (перезаписывается) + лог. Активный worklist (`new`) отделён от закрытых.

Запуск: python -m bot.update.monitor              # полная проверка + трекер + отчёт
        python -m bot.update.monitor --worklist   # только показать трекер (без записи)
"""
import asyncio
import datetime
import json
import os
import sys

import asyncpg

from ..config import load_config
from ..db import create_pool
from ..logger import logger

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_SLOVAR = os.path.join(_ROOT, "data", "slovar_svodnyy.json")
_REPORT = os.path.join(_ROOT, "docs", "NOVELTY.md")


def _slovar_semeystva(path: str = _SLOVAR) -> set:
    """Множество известных поиску семейств (ключи словаря)."""
    return set(json.load(open(path, encoding="utf-8")).keys())


async def proverit(pool: asyncpg.Pool, schema: str, report_path: str = _REPORT) -> dict:
    slovar = _slovar_semeystva()
    async with pool.acquire() as con:
        # новые СЕМЕЙСТВА: есть в БД, нет в словаре
        rows = await con.fetch(
            f"select semeystvo, count(*) n, min(imya) primer from {schema}.products "
            f"where semeystvo is not null and semeystvo <> '' group by semeystvo"
        )
        novye_sem = sorted(
            [(r["semeystvo"], r["n"], r["primer"]) for r in rows if r["semeystvo"] not in slovar],
            key=lambda x: -x[1],
        )

        # новые ПОДГРУППЫ: относительно известного набора (посев на первом прогоне)
        await con.execute(
            f"create table if not exists {schema}.known_podgruppy ("
            f"  podgruppa text primary key, seen_at timestamptz default now())"
        )
        known = {r["podgruppa"] for r in
                 await con.fetch(f"select podgruppa from {schema}.known_podgruppy")}
        cur_pg = await con.fetch(
            f"select podgruppa, count(*) n from {schema}.products "
            f"where podgruppa is not null and podgruppa <> '' group by podgruppa"
        )
        if not known:  # первый прогон — посев, без ложных срабатываний
            posev = [r["podgruppa"] for r in cur_pg]
            if posev:
                await con.executemany(
                    f"insert into {schema}.known_podgruppy (podgruppa) values ($1) "
                    f"on conflict do nothing", [(p,) for p in posev]
                )
            novye_pg = []
            logger.info(f"Монитор новизны: первый прогон — посев {len(posev)} подгрупп (baseline).")
        else:
            novye_pg = sorted(
                [(r["podgruppa"], r["n"]) for r in cur_pg if r["podgruppa"] not in known],
                key=lambda x: -x[1],
            )
            if novye_pg:
                await con.executemany(
                    f"insert into {schema}.known_podgruppy (podgruppa) values ($1) "
                    f"on conflict do nothing", [(p, ) for p, _ in novye_pg]
                )

        # этап 23 — накопительный трекер семейств (+ авто-закрытие внесённых в словарь)
        active, closed = await _track_semeystva(con, schema, novye_sem, slovar)

    _zapisat_otchet(report_path, active, closed, novye_pg)

    active_t = [(r["semeystvo"], r["pozicij"], r["primer"]) for r in active]
    if active_t or novye_pg:
        logger.warning(
            f"НОВИЗНА: активных семейств {len(active_t)} "
            f"({', '.join(f for f, _, _ in active_t) or '—'}), "
            f"новых подгрупп {len(novye_pg)} "
            f"({', '.join(p for p, _ in novye_pg) or '—'}) → нужна словарная правка, см. {os.path.basename(report_path)}"
        )
    else:
        logger.info("Монитор новизны: активных новых семейств/подгрупп нет — словарь актуален.")

    return {"new_families": active_t, "new_subgroups": novye_pg,
            "closed_families": [r["semeystvo"] for r in closed], "report": report_path}


async def _track_semeystva(con: asyncpg.Connection, schema: str, novye_sem: list, slovar: set):
    """Этап 23: накопительный трекер `novelty_families`.

    Активные (`status='new'`) — семейства в БД, которых нет в словаре: рабочий список «по чему
    писать словарь». Апсерт не плодит дублей и сохраняет `first_seen`. Авто-закрытие: семейство,
    появившееся в словаре, помечается `внесено` (в `novye_sem` оно уже не попадёт, т.к. в словаре)."""
    await con.execute(
        f"create table if not exists {schema}.novelty_families ("
        f"  semeystvo text primary key,"
        f"  first_seen timestamptz default now(),"
        f"  last_seen timestamptz default now(),"
        f"  pozicij int,"
        f"  primer text,"
        f"  status text default 'new')"
    )
    # 1) апсерт активных новинок: первый раз — INSERT (first_seen=now), повтор — обновить
    #    last_seen/счётчик/пример, first_seen НЕ трогать, статус вернуть в 'new'
    if novye_sem:
        await con.executemany(
            f"insert into {schema}.novelty_families "
            f"  (semeystvo, pozicij, primer, first_seen, last_seen, status) "
            f"values ($1, $2, $3, now(), now(), 'new') "
            f"on conflict (semeystvo) do update set "
            f"  last_seen = now(), pozicij = excluded.pozicij, "
            f"  primer = excluded.primer, status = 'new'",
            [(s, n, p) for s, n, p in novye_sem],
        )
    # 2) авто-закрытие: трекуемые 'new', которые ТЕПЕРЬ есть в словаре → 'внесено'
    tracked_new = await con.fetch(
        f"select semeystvo from {schema}.novelty_families where status = 'new'"
    )
    zakryt = [r["semeystvo"] for r in tracked_new if r["semeystvo"] in slovar]
    if zakryt:
        await con.execute(
            f"update {schema}.novelty_families set status = 'внесено', last_seen = now() "
            f"where semeystvo = any($1::text[])",
            zakryt,
        )
        logger.info(f"Трекер: авто-закрыто (внесено в словарь): {', '.join(zakryt)}")
    # 3) актуальные списки для отчёта
    active = await con.fetch(
        f"select semeystvo, pozicij, primer, first_seen from {schema}.novelty_families "
        f"where status = 'new' order by first_seen, pozicij desc"
    )
    closed = await con.fetch(
        f"select semeystvo, first_seen, last_seen from {schema}.novelty_families "
        f"where status = 'внесено' order by last_seen desc"
    )
    return active, closed


def _zapisat_otchet(path: str, active: list, closed: list, novye_pg: list) -> None:
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [f"# Новизна прайса — трекер новых семейств\n", f"_Обновлено: {ts}_\n"]

    if not active and not novye_pg:
        lines.append("\n**Активной новизны нет** — все семейства покрыты словарём, новых подгрупп нет.\n")
    else:
        if active:
            lines.append("\n## Рабочий список: новые семейства (НЕТ в словаре — нужны синонимы)\n")
            lines.append("\n| Семейство | Позиций | Впервые | Пример |\n|---|---|---|---|\n")
            for r in active:
                fs = r["first_seen"].strftime("%d.%m.%Y") if r["first_seen"] else "—"
                lines.append(f"| `{r['semeystvo']}` | {r['pozicij']} | {fs} | {r['primer']} |\n")
            lines.append(
                "\n**Действие:** добавить эти семейства в `data/slovar_svodnyy.json` с народными "
                "синонимами (субагенты Haiku, см. заметку в корневом `CLAUDE.md`). До этого позиции "
                "ищутся по прямому имени и штрихкоду, но не по разговорным названиям. После внесения "
                "в словарь следующий прогон монитора закроет семейство автоматически.\n"
            )
        if novye_pg:
            lines.append("\n## Новые подгруппы Форы\n")
            lines.append("\n| Подгруппа | Позиций |\n|---|---|\n")
            for p, n in novye_pg:
                lines.append(f"| {p} | {n} |\n")

    if closed:
        lines.append("\n## Внесены в словарь (авто-закрыто)\n")
        lines.append("\n| Семейство | Впервые | Закрыто |\n|---|---|---|\n")
        for r in closed:
            fs = r["first_seen"].strftime("%d.%m.%Y") if r["first_seen"] else "—"
            ls = r["last_seen"].strftime("%d.%m.%Y") if r["last_seen"] else "—"
            lines.append(f"| `{r['semeystvo']}` | {fs} | {ls} |\n")

    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w", encoding="utf-8").write("".join(lines))


async def _pokazat_worklist(pool: asyncpg.Pool, schema: str) -> None:
    """CLI --worklist: печатает трекер (read-only, без пересчёта/записи)."""
    try:
        active = await pool.fetch(
            f"select semeystvo, pozicij, primer, first_seen from {schema}.novelty_families "
            f"where status = 'new' order by first_seen, pozicij desc"
        )
        closed = await pool.fetch(
            f"select semeystvo, last_seen from {schema}.novelty_families "
            f"where status = 'внесено' order by last_seen desc"
        )
    except asyncpg.UndefinedTableError:
        logger.info("Трекер `novelty_families` ещё не создан — прогонов монитора не было.")
        return
    if not active:
        logger.info("Worklist пуст: активных новых семейств нет.")
    else:
        logger.info(f"Worklist — активные новые семейства (нужен словарь): {len(active)}")
        for r in active:
            fs = r["first_seen"].strftime("%d.%m.%Y") if r["first_seen"] else "—"
            logger.info(f"  • {r['semeystvo']} — {r['pozicij']} поз., впервые {fs} (пример: {r['primer']})")
    if closed:
        logger.info(f"Закрыто (внесено в словарь): {', '.join(r['semeystvo'] for r in closed)}")


async def _main() -> None:
    cfg = load_config()
    pool = await create_pool(cfg)
    try:
        if "--worklist" in sys.argv:
            await _pokazat_worklist(pool, cfg.pg.schema)
        else:
            await proverit(pool, cfg.pg.schema)
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(_main())
