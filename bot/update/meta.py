# -*- coding: utf-8 -*-
"""Метаданные прайса (этап 20): дата актуальности. Таблица-синглтон `price_meta`.

`price_date` — когда Фора выгрузила прайс (из имени файла `…YYYY-MM-DDThh-mm-ss…`,
fallback — mtime файла). `updated_at` — когда наш sync применил файл. Бот отдаёт дату
покупателю, чтобы не выдавать вчерашнее за свежее (жёсткое требование PROJECT.md).

Пока дата одна на весь прайс (оба магазина в одном файле). Когда клиент разнесёт точки
на разные серверы/файлы (веха 2), сюда добавятся отдельные даты по магазину.
"""
import datetime
import os
import re

import asyncpg

_RE_TS = re.compile(r"(\d{4})-(\d{2})-(\d{2})T(\d{2})-(\d{2})-(\d{2})")
_DEFAULT = "18.07.2026"  # fallback, если метаданных ещё нет


def data_iz_faila(path: str):
    """Дата выгрузки прайса: из имени файла (…2026-07-18T11-29-30…) либо mtime."""
    m = _RE_TS.search(os.path.basename(path))
    if m:
        y, mo, d, h, mi, s = map(int, m.groups())
        try:
            return datetime.datetime(y, mo, d, h, mi, s)
        except ValueError:
            pass
    try:
        return datetime.datetime.fromtimestamp(os.path.getmtime(path))
    except OSError:
        return None


async def zapisat(con: asyncpg.Connection, schema: str, price_date, file_name: str) -> None:
    """Апсертит единственную строку метаданных (id=1)."""
    await con.execute(
        f"create table if not exists {schema}.price_meta ("
        f"  id smallint primary key,"
        f"  price_date timestamptz,"
        f"  updated_at timestamptz,"
        f"  file_name text)"
    )
    await con.execute(
        f"insert into {schema}.price_meta (id, price_date, updated_at, file_name) "
        f"values (1, $1, now(), $2) "
        f"on conflict (id) do update set "
        f"  price_date = excluded.price_date, updated_at = now(), file_name = excluded.file_name",
        price_date, file_name,
    )


async def zagruzit_datu(pool: asyncpg.Pool, schema: str, default: str = _DEFAULT) -> str:
    """Дата актуальности для бота в формате dd.mm.yyyy. Если метаданных нет — default."""
    try:
        row = await pool.fetchrow(f"select price_date from {schema}.price_meta where id = 1")
    except asyncpg.UndefinedTableError:
        return default
    if row and row["price_date"]:
        return row["price_date"].strftime("%d.%m.%Y")
    return default
