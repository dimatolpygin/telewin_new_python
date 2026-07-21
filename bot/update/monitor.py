# -*- coding: utf-8 -*-
"""Этап 21 — монитор новизны прайса.

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

Отчёт — `docs/NOVELTY.md` (перезаписывается) + лог. Если новизны нет — так и пишет.

Запуск: python -m bot.update.monitor
"""
import asyncio
import datetime
import json
import os

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

    _zapisat_otchet(report_path, novye_sem, novye_pg)

    if novye_sem or novye_pg:
        logger.warning(
            f"НОВИЗНА: новых семейств {len(novye_sem)} "
            f"({', '.join(f for f, _, _ in novye_sem) or '—'}), "
            f"новых подгрупп {len(novye_pg)} "
            f"({', '.join(p for p, _ in novye_pg) or '—'}) → нужна словарная правка, см. {os.path.basename(report_path)}"
        )
    else:
        logger.info("Монитор новизны: новых семейств/подгрупп нет — словарь актуален.")

    return {"new_families": novye_sem, "new_subgroups": novye_pg, "report": report_path}


def _zapisat_otchet(path: str, novye_sem: list, novye_pg: list) -> None:
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [f"# Новизна прайса — отчёт монитора\n", f"_Обновлено: {ts}_\n"]
    if not novye_sem and not novye_pg:
        lines.append("\n**Новизны нет** — все семейства покрыты словарём, новых подгрупп не появилось.\n")
    else:
        if novye_sem:
            lines.append("\n## Новые семейства (НЕТ в словаре — нужны синонимы)\n")
            lines.append("\n| Семейство | Позиций | Пример |\n|---|---|---|\n")
            for f, n, primer in novye_sem:
                lines.append(f"| `{f}` | {n} | {primer} |\n")
            lines.append(
                "\n**Действие:** добавить эти семейства в `data/slovar_svodnyy.json` с народными "
                "синонимами (субагенты Haiku, см. заметку в корневом `CLAUDE.md`). До этого позиции "
                "ищутся по прямому имени и штрихкоду, но не по разговорным названиям.\n"
            )
        if novye_pg:
            lines.append("\n## Новые подгруппы Форы\n")
            lines.append("\n| Подгруппа | Позиций |\n|---|---|\n")
            for p, n in novye_pg:
                lines.append(f"| {p} | {n} |\n")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w", encoding="utf-8").write("".join(lines))


async def _main() -> None:
    cfg = load_config()
    pool = await create_pool(cfg)
    try:
        await proverit(pool, cfg.pg.schema)
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(_main())
