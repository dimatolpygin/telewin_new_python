# -*- coding: utf-8 -*-
"""Этап 22 — валидация файла прайса ПЕРЕД любыми записями в БД.

Зачем отдельным шагом: FTP регулярно отдаёт пустышки 51 200 Б вместо реальных ~2.4–3.2 МБ
(заметка в корневом CLAUDE.md), а битый/обрезанный/не тот файл не должен попасть в БД и
затереть боевые данные. Поэтому оркестратор (`run.py`) зовёт `proverit(path)` ДО `sync`:
провал → апдейт отменяется, БД остаётся целой.

Проверяем дёшево и без записей:
  - файл существует и весит правдоподобно (> MIN_RAZMER — отсекает пустышку 51 200 Б);
  - открывается как .xls (иначе это HTML-заглушка/мусор с расширением .xls);
  - строк не меньше MIN_STROK (боевой ~11 864; обрезанный/битый — единицы);
  - колонок не меньше NUZHNO_KOLONOK (раскладка 11-кол; автоопределение 8↔11 — этап 18).
"""
import os

import xlrd

MIN_RAZMER = 500_000       # байт: пустышка FTP = 51 200, боевой = 2.4–3.2 МБ → порог с запасом
MIN_STROK = 1000           # боевой ~11 864; обрезанный/битый файл — заметно меньше
NUZHNO_KOLONOK = 11        # 11-колоночная раскладка 18.07 (этап 18 добавит 8-кол)


class ValidationError(Exception):
    """Файл не годится для апдейта — цикл должен быть отменён без записей в БД."""


def proverit(path: str) -> dict:
    """Валидирует .xls прайса. Возвращает сводку {razmer, strok, kolonok}
    либо бросает ValidationError с человекочитаемой причиной."""
    if not os.path.isfile(path):
        raise ValidationError(f"файл не найден: {path}")

    razmer = os.path.getsize(path)
    if razmer < MIN_RAZMER:
        raise ValidationError(
            f"файл подозрительно мал: {razmer} Б < {MIN_RAZMER} Б — вероятно, пустышка FTP"
        )

    try:
        book = xlrd.open_workbook(path, encoding_override="cp1251")
        sheet = book.sheet_by_index(0)
    except Exception as e:  # xlrd бросает разные типы на битом/не-xls файле
        raise ValidationError(f"не открывается как .xls: {e}")

    if sheet.nrows < MIN_STROK:
        raise ValidationError(
            f"мало строк: {sheet.nrows} < {MIN_STROK} — файл обрезан или битый"
        )
    if sheet.ncols < NUZHNO_KOLONOK:
        raise ValidationError(
            f"мало колонок: {sheet.ncols} < {NUZHNO_KOLONOK} — не 11-колоночная раскладка"
        )

    return {"razmer": razmer, "strok": sheet.nrows, "kolonok": sheet.ncols}
