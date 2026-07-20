# -*- coding: utf-8 -*-
"""Нормализация и стемминг. Порт из poisk2.py (norm/stems/family_of).
Стеммер — snowball russian (ключевое отличие от TS-`natural`: даёт +7% top-1).
"""
import re
import snowballstemmer

_st = snowballstemmer.stemmer("russian")

# Латинские гомоглифы -> кириллица: «болт м5» ↔ «Болт M 5».
_GOMO = str.maketrans("acekmopxyABCEKMHOPTXY", "асекморхуАВСЕКМНОРТХУ")

_STOP = {
    "и", "в", "на", "по", "для", "с", "от", "до", "у", "есть", "ли", "нужен",
    "нужны", "нужно", "сколько", "стоит", "что", "какой", "мне", "а", "the",
}


def norm(s: str) -> str:
    return s.lower().replace("ё", "е").translate(_GOMO)


def stems(s: str) -> set:
    out = set()
    for t in re.findall(r"[а-яa-z0-9][а-яa-z0-9.,/]*", norm(s)):
        t = t.strip(".,")
        if t and t not in _STOP:
            out.add(_st.stemWord(t))
    return out


def family_of(name: str) -> str:
    p = name.split()
    if not p:
        return "?"
    w = p[0].strip(",.")
    if w.upper() == "ШС" and len(p) > 1:
        return "ШС " + p[1].strip(",.")
    return w
