# -*- coding: utf-8 -*-
"""Триграммное сходство слов (этап 8) — паритет с pg_trgm.

Боевой путь — фаззи-канал в Postgres (`pg_trgm`, оператор `%`/`similarity`).
Для офлайн-прогона (probe/test_subagenty по json) считаем ту же метрику в Python,
чтобы метрика стенда совпадала с БД. Определение как в pg_trgm: слово дополняется
двумя пробелами слева и одним справа, набор триграмм; similarity = |A∩B| / |A∪B|.
"""
import re

_WORD = re.compile(r"[a-zа-я0-9]+")


def trigrams(s: str) -> set:
    tg = set()
    for w in _WORD.findall(s.lower()):
        w2 = "  " + w + " "
        for i in range(len(w2) - 2):
            tg.add(w2[i:i + 3])
    return tg


def similarity(a: str, b: str) -> float:
    """pg_trgm-совместимое сходство двух строк в [0,1]."""
    A, B = trigrams(a), trigrams(b)
    if not A and not B:
        return 0.0
    inter = len(A & B)
    return inter / (len(A) + len(B) - inter)


def _levenshtein(a: str, b: str) -> int:
    """Расстояние Левенштейна (итеративно, O(len(a)*len(b)) памяти O(len(b)))."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def edit_ratio(a: str, b: str) -> float:
    """Нормализованное сходство по Левенштейну в [0,1]: 1 - dist/max(len).
    Точнее триграмм на коротких словах («малоток»~«молоток»=0.86 против 0.46)."""
    if not a and not b:
        return 0.0
    return 1.0 - _levenshtein(a, b) / max(len(a), len(b))


def word_sim(a: str, b: str) -> float:
    """Сходство слов для резолва семейства по опечатке: максимум из триграммного
    (ловит перестановки/вставки) и левенштейновского (точен на коротких словах)."""
    return max(similarity(a, b), edit_ratio(a, b))
