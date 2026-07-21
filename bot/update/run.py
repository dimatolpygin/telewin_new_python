# -*- coding: utf-8 -*-
"""Этап 22 — оркестратор ежедневного обновления прайса.

Один идемпотентный вызов проходит весь цикл:

    fetch → validate → parse → upsert → embed → monitor → дата

- **fetch** (этап 17): `--ftp` скачивает свежий файл с FTP Форы во временную папку (размер-гейт
  до загрузки, `bot/update/istochnik.py`); без флага — локально (аргумент CLI / `PRICE_XLS`).
- **validate** (этап 22, `validate.proverit`) — ДО любых записей: пустышка/битый/не тот
  формат → `ValidationError` → цикл отменён, БД цела, в логе причина.
- **parse → upsert → дата → embed → monitor** — делегируются в `sync()` (этапы 19/20/21).
  run.py добавляет только валидацию, обработку ошибок и сводку — чтобы diff-логика жила в
  одном месте (единый источник правды по сопоставлению позиций).

Идемпотентность наследуется от `sync`: diff по штрихкоду внутри одной транзакции, повторный
прогон того же файла даёт 0 вставок/правок/удалений (всё skip), вектора не трогаются.

Запуск:
    python -m bot.update.run --ftp                # скачать свежий с FTP Форы и прогнать цикл
    python -m bot.update.run --ftp --skip-known   # + пропуск, если файл уже применён (для cron)
    python -m bot.update.run                      # файл из PRICE_XLS (.env)
    python -m bot.update.run <путь_к_xls>          # явный файл
    python -m bot.update.run <путь> --no-embed     # без re-embed (отладка, без денег/времени)
    python -m bot.update.run <путь> --no-monitor   # без монитора новизны

Расписание (этап 24): `scripts/update.sh` (POSIX, flock/lock) зовёт этот модуль с
`--ftp --skip-known`; systemd-timer/cron — в `deploy/`, инструкция — `docs/DEPLOY_UPDATE.md`.

Расписание (вне скоупа этапа 22 — только точка подключения): планировщик/cron зовёт эту
команду раз в сутки, напр. `0 6 * * *  cd /path/tgbot_py && python -m bot.update.run`.
"""
import asyncio
import datetime
import os
import sys
import time

import asyncpg

from ..config import load_config
from ..db import create_pool
from ..logger import logger
from . import validate
from .istochnik import IstochnikError
from .sync import sync

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_REPORT = os.path.join(_ROOT, "docs", "UPDATE_RUN.md")


def _lokalnyy_put(cfg) -> str:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if args:
        return args[0]
    if cfg.price_xls:
        return cfg.price_xls
    raise SystemExit(
        "Не задан файл прайса: python -m bot.update.run <путь> | --ftp | PRICE_XLS в .env"
    )


async def _uzhe_primenen(cfg, imya: str) -> bool:
    """True, если файл с таким именем уже применён (по price_meta.file_name)."""
    pool = await create_pool(cfg)
    try:
        row = await pool.fetchrow(
            f"select file_name from {cfg.pg.schema}.price_meta where id = 1"
        )
        return bool(row and row["file_name"] == imya)
    except asyncpg.UndefinedTableError:
        return False
    finally:
        await pool.close()


async def _poluchit_ili_propustit(cfg):
    """Возвращает (путь, None) для обработки, либо (None, имя) — если файл уже применён
    и задан `--skip-known` (этап 24: не гонять цикл на неизменившемся прайсе)."""
    skip = "--skip-known" in sys.argv
    if "--ftp" in sys.argv:
        from .istochnik import sozdat_ftp_istochnik
        src = sozdat_ftp_istochnik(cfg)
        if skip and await _uzhe_primenen(cfg, src.svezhee_imya()):
            return None, src.svezhee_imya()
        return src.poluchit_svezhiy(), None
    path = _lokalnyy_put(cfg)
    imya = os.path.basename(path)
    if skip and await _uzhe_primenen(cfg, imya):
        return None, imya
    return path, None


async def run(path: str | None = None, apply_embed: bool = True,
              monitor: bool = True) -> dict:
    """Полный цикл обновления. Возвращает сводку `sync` (+ novelty). Бросает SystemExit(2)
    на провале валидации (БД при этом не тронута)."""
    cfg = load_config()
    t0 = time.monotonic()

    # 1) fetch — источник файла (этап 17): FTP `--ftp` или локальный путь;
    #    `--skip-known` (этап 24) — пропустить, если файл уже применён
    if path is None:
        try:
            path, propustit = await _poluchit_ili_propustit(cfg)
        except IstochnikError as e:
            logger.error(f"ОТМЕНА: не удалось получить файл прайса — {e}. БД НЕ изменена.")
            raise SystemExit(2)
        if propustit:
            logger.info(f"Свежий прайс {propustit} уже применён — цикл пропущен (skip-known).")
            return {"skipped": propustit}
    logger.info(f"=== Обновление прайса: {os.path.basename(path)} ===")

    # 2) validate — ДО любых записей в БД
    try:
        info = validate.proverit(path)
    except validate.ValidationError as e:
        logger.error(f"ОТМЕНА: файл не прошёл валидацию — {e}. БД НЕ изменена.")
        raise SystemExit(2)
    logger.info(
        f"Валидация ОК: {info['razmer']} Б, {info['strok']} строк, {info['kolonok']} колонок."
    )

    # 3) parse → upsert → дата → embed → monitor (всё внутри sync)
    itog = await sync(path, apply_embed=apply_embed, monitor=monitor)

    dt = time.monotonic() - t0
    nov = itog.get("novelty") or {}
    novye_sem = nov.get("new_families", []) or []
    novye_pg = nov.get("new_subgroups", []) or []
    reembed = itog["insert"] + itog["upd_rename"]  # позиции, ушедшие на пересчёт вектора

    svodka = (
        "=== СВОДКА обновления ===\n"
        f"  файл            : {os.path.basename(path)}\n"
        f"  изменено фактов : {itog['upd_fact']}\n"
        f"  переименовано   : {itog['upd_rename']} (→ re-embed)\n"
        f"  новых позиций   : {itog['insert']} (→ re-embed)\n"
        f"  на re-embed     : {reembed}\n"
        f"  удалено         : {itog['delete']}\n"
        f"  без изменений   : {itog['skip']}\n"
        f"  всего строк     : {itog['rows']} (с вектором {itog['embedding']})\n"
        f"  новых семейств  : {len(novye_sem)}"
        f"{' — ' + ', '.join(f for f, _, _ in novye_sem) if novye_sem else ''}\n"
        f"  новых подгрупп  : {len(novye_pg)}"
        f"{' — ' + ', '.join(p for p, _ in novye_pg) if novye_pg else ''}\n"
        f"  дата прайса     : {itog['price_date']}\n"
        f"  время цикла     : {dt:.1f} c"
    )
    logger.info(svodka)
    _zapisat_svodku(path, itog, reembed, novye_sem, novye_pg, dt)
    return itog


def _zapisat_svodku(path, itog, reembed, novye_sem, novye_pg, dt) -> None:
    """Пишет сводку последнего прогона в docs/UPDATE_RUN.md (перезаписывается)."""
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        "# Обновление прайса — сводка последнего прогона\n",
        f"_Прогон: {ts}; цикл {dt:.1f} c_\n\n",
        f"- **Файл:** `{os.path.basename(path)}`\n",
        f"- **Дата прайса:** {itog['price_date']}\n",
        f"- **Изменено фактов:** {itog['upd_fact']}\n",
        f"- **Переименовано (→ re-embed):** {itog['upd_rename']}\n",
        f"- **Новых позиций (→ re-embed):** {itog['insert']}\n",
        f"- **Всего на re-embed:** {reembed}\n",
        f"- **Удалено:** {itog['delete']}\n",
        f"- **Без изменений:** {itog['skip']}\n",
        f"- **Всего строк:** {itog['rows']} (с вектором {itog['embedding']})\n",
        f"- **Новых семейств:** {len(novye_sem)}"
        f"{' — ' + ', '.join(f for f, _, _ in novye_sem) if novye_sem else ''}\n",
        f"- **Новых подгрупп:** {len(novye_pg)}"
        f"{' — ' + ', '.join(p for p, _ in novye_pg) if novye_pg else ''}\n",
    ]
    if novye_sem or novye_pg:
        lines.append("\n> Подробности новизны и что делать со словарём — `docs/NOVELTY.md`.\n")
    os.makedirs(os.path.dirname(_REPORT), exist_ok=True)
    open(_REPORT, "w", encoding="utf-8").write("".join(lines))


def _main() -> None:
    apply_embed = "--no-embed" not in sys.argv
    monitor = "--no-monitor" not in sys.argv
    asyncio.run(run(apply_embed=apply_embed, monitor=monitor))


if __name__ == "__main__":
    _main()
