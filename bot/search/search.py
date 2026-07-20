# -*- coding: utf-8 -*-
"""Поиск по прайсу: словарь домена + атрибуты + прямые каналы (артикул, штрихкод)
   + опциональный recall-канал по подгруппе Форы. БЕЗ ИИ — чистый детерминизм.

Порт poisk.py (стенд 1, эталон 92% top-1 на snowball) с двумя добавлениями из
poisk2.py: прямой канал штрихкода (EAN) и канал подгруппы (по флагу, тюнинг — этап 5).

Источник данных — список товаров (products.json на этапе 1, Postgres на этапе 2),
формат ключей как в products.json: artikul, shtrihkod, imya, edinica, proizvoditel,
cena, ostatok_obshiy, ostatok_mikro, ostatok_berez, semeystvo, gruppa, podgruppa.
"""
import json
import os
import re
import collections

from .normalize import stems, family_of, norm
from .attributes import razobrat

_DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data")


def load_products_json(path: str | None = None) -> list[dict]:
    """Загрузка товаров из products.json (этап 1)."""
    path = path or os.path.join(_DATA, "products.json")
    return json.load(open(path, encoding="utf-8"))


class Poisk:
    def __init__(self, tovary: list[dict], data_dir: str | None = None):
        data_dir = data_dir or _DATA
        self.slovar = json.load(open(os.path.join(data_dir, "slovar_svodnyy.json"), encoding="utf-8"))
        self.sleng = json.load(open(os.path.join(data_dir, "sleng_razmerov.json"), encoding="utf-8"))

        # индексная строка: оригинальный товар + вычисленные поля поиска
        self.rows = []
        for t in tovary:
            imya = t.get("imya", "")
            self.rows.append({
                "t": t,
                "семейство": t.get("semeystvo") or family_of(imya),
                "атр": razobrat(imya),
                "стемы": stems(imya),
                "стемы_подгр": stems(t.get("podgruppa", "")),
            })
        self.po_sem = collections.defaultdict(list)
        for row in self.rows:
            self.po_sem[row["семейство"]].append(row)

        # обратный индекс канала подгруппы: подгруппа -> её стемы
        self.podgr_stems = {}
        for row in self.rows:
            self.podgr_stems.setdefault(row["t"].get("podgruppa", ""), row["стемы_подгр"])

        # варианты названия семейства: само имя, канон, каждый синоним
        self.varianty = {}
        self.slovar_sem = {}
        for f, e in self.slovar.items():
            vs = [f, e.get("канон", "")] + list(e.get("синонимы", []))
            self.varianty[f] = [stems(v) for v in vs if v and stems(v)]
            all_st = set()
            for v in self.varianty[f]:
                all_st |= v
            self.slovar_sem[f] = all_st

    @classmethod
    def from_json(cls, path: str | None = None, data_dir: str | None = None) -> "Poisk":
        return cls(load_products_json(path), data_dir)

    @property
    def размер_базы(self) -> int:
        return len(self.rows)

    def atributy_zaprosa(self, q: str) -> dict:
        a = razobrat(q)
        qn = norm(q)
        for slovo, mm in self.sleng["длина_мм"].items():
            if slovo in qn:
                a["длина_мм"] = float(mm)
        for slovo, m in self.sleng["резьба_M"].items():
            if re.search(rf"\b{slovo}\b", qn):
                a.setdefault("резьба_m", float(m))
        for slovo, d in self.sleng["дюймы"].items():
            if slovo.replace("_", " ") in qn:
                a.setdefault("дюймы", d)
        if "пол дюйма" in qn or "полдюйма" in qn:
            a["дюймы"] = "1/2"
        a["_числа"] = {float(x.replace(",", ".")) for x in re.findall(r"\d+(?:[.,]\d+)?", qn)}
        return a

    def semeystva_kandidaty(self, qs: set):
        res = []
        for f, vs in self.varianty.items():
            best = 0.0
            for v in vs:
                if not v:
                    continue
                cov = len(v & qs) / len(v)   # полностью ли назван вариант
                best = max(best, cov)
            if best == 0:
                continue
            explained = len(self.slovar_sem[f] & qs) / max(1, len(qs))
            res.append((best + 0.5 * explained, f))
        res.sort(reverse=True)
        return res

    def podgruppy_po_zaprosu(self, qs: set):
        """Подгруппы, чьи слова совпали со словами запроса. Recall-канал Форы."""
        res = []
        for sub, ss in self.podgr_stems.items():
            if not ss:
                continue
            hit = len(qs & ss)
            if hit:
                res.append((hit / len(ss), sub))
        res.sort(reverse=True)
        return res

    def iskat(self, q: str, top: int = 5, use_podgr: bool = False):
        """Возвращает (список_товаров, канал). Товары — оригинальные dict из products.json."""
        qs = stems(q)
        qa = self.atributy_zaprosa(q)
        chisla = qa.pop("_числа", set())

        # прямой канал: штрихкод (EAN, 8-13 цифр) — самый специфичный
        for m in re.findall(r"\b\d{8,13}\b", q):
            hit = [r["t"] for r in self.rows if r["t"].get("shtrihkod") == m]
            if hit:
                return hit[:top], "штрихкод"
        # прямой канал: артикул (4-6 цифр)
        for m in re.findall(r"\b\d{4,6}\b", q):
            hit = [r["t"] for r in self.rows
                   if r["t"].get("artikul") == m or str(r["t"].get("artikul", "")).lstrip("0") == m.lstrip("0")]
            if hit:
                return hit[:top], "артикул"

        kand = self.semeystva_kandidaty(qs)
        sem_score = {f: s for s, f in kand}

        semi = []
        if kand and kand[0][0] >= 0.6:
            porog = kand[0][0] - 0.35
            semi = [f for s, f in kand if s >= porog][:4]

        # канал подгруппы (опционально): добавляет строки-кандидаты по совпавшим подгруппам
        subs = set()
        sub_score = {}
        if use_podgr:
            for s, sub in self.podgruppy_po_zaprosu(qs):
                sub_score[sub] = s
            if sub_score:
                best_sub = max(sub_score.values())
                subs = {sub for sub, s in sub_score.items() if s >= best_sub - 0.2 and s > 0}

        if not semi and not subs:
            return [], "не найдено"

        def ball(row, in_sem: bool, in_sub: bool) -> float:
            s = 0.0
            if in_sem:
                s += 3.0 * sem_score[row["семейство"]]
            if in_sub:
                s += 2.0 * sub_score[row["t"]["podgruppa"]]
            # совпадение атрибутов запроса и строки
            for k, v in qa.items():
                if k in row["атр"]:
                    s += 4.0 if row["атр"][k] == v else -1.5
            # голые числа из запроса против атрибутов строки
            for v in row["атр"].values():
                if isinstance(v, float) and v in chisla:
                    s += 2.0
            # пересечение слов запроса с именем товара
            s += 1.2 * len(qs & row["стемы"])
            # наличие товара — при прочих равных
            try:
                s += 0.3 if float(row["t"].get("ostatok_obshiy") or 0) > 0 else 0
            except (ValueError, TypeError):
                pass
            return s

        # Обход как в poisk.py: сперва строки семейств-кандидатов в порядке скора
        # семейства (важно для tie-break при равных баллах), затем — канал подгруппы.
        scored = []
        seen = set()
        for f in semi:
            for row in self.po_sem.get(f, []):
                in_sub = use_podgr and row["t"].get("podgruppa", "") in subs
                scored.append((ball(row, True, in_sub), row["t"]))
                seen.add(id(row))
        if use_podgr and subs:
            for row in self.rows:
                if id(row) in seen:
                    continue
                if row["t"].get("podgruppa", "") in subs:
                    scored.append((ball(row, False, True), row["t"]))
        scored.sort(key=lambda x: -x[0])

        kanal = "+".join(c for c in (
            "подгр" if (use_podgr and subs) else "",
            "словарь" if semi else "",
            "атрибуты",
        ) if c)
        return [t for _, t in scored[:top]], kanal
