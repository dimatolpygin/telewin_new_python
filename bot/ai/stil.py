# -*- coding: utf-8 -*-
"""Постобработка стиля ответа (этап 32). Промт задаёт тон и краткость, а гарантию
чистоты даёт КОД: модель всё равно иногда проставит длинное тире «—» или markdown
(`**жирный**`, заголовки, списки), даже когда промт это запрещает. Здесь их снимаем
регэкспами — детерминированно, на каждый ответ. Поиск/цифры не трогаем: работаем
только с готовым текстом реплики. Подход взят из референса `Downloads/vk0043/humanize.py`.
"""
import re

_TIRE_MEZHDU_CHISEL = re.compile(r"(\d)\s*[—–]\s*(\d)")   # 10 — 15  →  10-15
_TIRE_MEZHDU_SLOV = re.compile(r"\s*[—–]\s*")             # слово — слово  →  слово, слово
_ZHIRNYY = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")          # **жирный** / __жирный__
_KURSIV = re.compile(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)")  # *курсив* (не трогает **)
_ZAGOLOVOK = re.compile(r"^\s{0,3}#{1,6}\s*", re.M)        # ## Заголовок
_MARKER_SPISKA = re.compile(r"^\s{0,3}(?:[-*•]|\d{1,2}[.)])\s+", re.M)  # - пункт / 1. пункт
_MNOGO_PUSTYH = re.compile(r"\n\s*\n\s*\n+")              # 3+ переводов строки
_PROBELY = re.compile(r"[ \t]{2,}")


def ochistit_otvet(text: str) -> str:
    """Убрать из ответа модели длинное тире и markdown-разметку, вернуть чистый
    человеческий текст. Идемпотентна (повторный прогон ничего не меняет)."""
    if not text:
        return text
    # тире: сперва числовые диапазоны (дефис), потом тире между словами (запятая)
    text = _TIRE_MEZHDU_CHISEL.sub(r"\1-\2", text)
    text = _TIRE_MEZHDU_SLOV.sub(", ", text)
    # markdown-акценты — оставить только содержимое
    text = _ZHIRNYY.sub(lambda m: m.group(1) or m.group(2), text)
    text = _KURSIV.sub(r"\1", text)
    # заголовки и маркеры списков в начале строк — снять, содержимое оставить
    text = _ZAGOLOVOK.sub("", text)
    text = _MARKER_SPISKA.sub("", text)
    # подчистка артефактов
    text = re.sub(r"^\s*,\s*", "", text, flags=re.M)   # запятая в начале строки (после тире-замены)
    text = re.sub(r"\s+,", ",", text)                   # пробел перед запятой
    text = re.sub(r",\s*,", ",", text)                  # задвоенная запятая
    text = _MNOGO_PUSTYH.sub("\n\n", text)              # не больше одной пустой строки подряд
    text = _PROBELY.sub(" ", text)
    return text.strip()
