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


def soft_has(stem_set: set, w: str) -> bool:
    """Мягкое совпадение слова с множеством стемов: точное ИЛИ один — префикс
    другого (мин. 4 симв). Связывает сокращения прайса с полными формами:
    «алмаз.»↔«алмазный», «оцинк.»↔«оцинкованный». Порт softHas из search.ts."""
    if w in stem_set:
        return True
    if len(w) < 4:
        return False
    for x in stem_set:
        if len(x) < 4:
            continue
        short, long = (x, w) if len(x) < len(w) else (w, x)
        if long.startswith(short) and len(short) >= 4:
            return True
    return False


class Poisk:
    def __init__(self, tovary: list[dict], data_dir: str | None = None):
        data_dir = data_dir or _DATA
        self.slovar = json.load(open(os.path.join(data_dir, "slovar_svodnyy.json"), encoding="utf-8"))
        self.sleng = json.load(open(os.path.join(data_dir, "sleng_razmerov.json"), encoding="utf-8"))
        # рулевой словарь этапа 6: материал->подгруппа, сленг-слово->токен в имени
        mat = json.load(open(os.path.join(data_dir, "materialy.json"), encoding="utf-8"))
        self.material_podgr = mat.get("материал_подгруппа", {})
        self.slovo_v_imeni = mat.get("слово_в_имени", {})
        # материал-в-имени (этап 7): стем запроса -> материал; регэксп материала над именем.
        # Собираем обратный индекс: стем -> ключ материала и компилируем регэкспы имён.
        self.material_stems = {}
        self.material_re = {}
        for mkey, spec in mat.get("материал_в_имени", {}).items():
            self.material_re[mkey] = re.compile(spec["имя"])
            for st in spec["стемы"]:
                self.material_stems[st] = mkey

        # индексная строка: оригинальный товар + вычисленные поля поиска
        self.rows = []
        for t in tovary:
            imya = t.get("imya", "")
            imya_low = imya.lower()
            self.rows.append({
                "t": t,
                "семейство": t.get("semeystvo") or family_of(imya),
                "атр": razobrat(imya),
                "стемы": stems(imya),
                "стемы_подгр": stems(t.get("podgruppa", "")),
                # назначение по материалу (этап 7): {дерево/бетон/...} из имени товара
                "материалы": {mk for mk, rx in self.material_re.items() if rx.search(imya_low)},
            })
        self.po_sem = collections.defaultdict(list)
        for row in self.rows:
            self.po_sem[row["семейство"]].append(row)

        # обратный индекс канала подгруппы: подгруппа -> её стемы
        self.podgr_stems = {}
        for row in self.rows:
            self.podgr_stems.setdefault(row["t"].get("podgruppa", ""), row["стемы_подгр"])

        # индекс канала производителя: производитель -> его стемы и его строки
        self.proizv_stems = {}
        self.po_proizv = collections.defaultdict(list)
        for row in self.rows:
            pr = row["t"].get("proizvoditel", "")
            self.po_proizv[pr].append(row)
            if pr not in self.proizv_stems:
                self.proizv_stems[pr] = stems(pr)

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

    def rulevye(self, q: str):
        """Рулевые сигналы запроса (этап 6): подгруппы по слову-материалу и
        токены-в-имени по сленгу. Детект по границе слова в норм. запросе — мимо
        стеммера, чтобы «пп»/«пэ» не слипались с другими словами."""
        qn = norm(q)
        mat_subs = set()
        for slovo, subs in self.material_podgr.items():
            if re.search(rf"\b{re.escape(slovo)}\b", qn):
                mat_subs.update(subs)
        tokens = []
        for slovo, tok in self.slovo_v_imeni.items():
            if re.search(rf"\b{re.escape(slovo)}\b", qn):
                tokens.append(tok.lower())
        return mat_subs, tokens

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

    def proizvoditeli_po_zaprosu(self, qs: set):
        """Производители, чьё имя ПОЛНОСТЬЮ названо в запросе («что есть от бибера»).
        Полное покрытие + токен ≥3 симв — чтобы не ловить короткие/общие слова."""
        res = []
        for pr, ss in self.proizv_stems.items():
            if not ss or not pr:
                continue
            if ss <= qs and any(len(w) >= 3 for w in ss):
                res.append(pr)
        return res

    def iskat(self, q: str, top: int = 5, use_podgr: bool = False, use_slovar: bool = True,
              use_proizv: bool = True):
        """Возвращает (список_товаров, канал). Товары — оригинальные dict из products.json.

        use_slovar / use_podgr — для аблации вклада каналов (режимы 2A/2B/2C как в poisk2.py).
        Прямые каналы (штрихкод/артикул) работают всегда — они не зависят от словаря.
        """
        qs = stems(q)
        qa = self.atributy_zaprosa(q)
        chisla = qa.pop("_числа", set())
        mat_subs, name_tokens = self.rulevye(q)
        # материал, названный в запросе (этап 7): «по дереву/бетону/металлу…»
        zapros_materialy = {self.material_stems[s] for s in qs if s in self.material_stems}
        self._last_top = 0.0  # диагностика: балл лучшего кандидата (для калибровки порога)

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

        sem_score = {}
        semi = []
        if use_slovar:
            kand = self.semeystva_kandidaty(qs)
            sem_score = {f: s for s, f in kand}
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

        # канал производителя (fallback-recall): срабатывает, когда словарь не дал
        # семейства, а в запросе полностью назван производитель («что есть от бибера»).
        proizv = []
        if use_proizv and not semi:
            proizv = self.proizvoditeli_po_zaprosu(qs)

        if not semi and not subs and not proizv:
            return [], "не найдено"

        if proizv and not semi and not subs:
            # чистый запрос по производителю: отдаём его товары (в наличии — выше)
            rows = [r["t"] for pr in proizv for r in self.po_proizv.get(pr, [])]
            rows.sort(key=lambda t: -(float(t.get("ostatok_obshiy") or 0)))
            return rows[:top], "производитель"

        # Уточняющие слова-подтипы (порт из search.ts): слово РАЗЛИЧАЕТ товар, если
        # встречается у МЕНЬШИНСТВА строк семейств-кандидатов. «саморез» есть у всех
        # саморезов (не различает), «чёрный»/«оцинк» — у части (различает подтип).
        kand_rows = []
        for f in semi:
            kand_rows.extend(self.po_sem.get(f, []))
        utochn = set()
        if kand_rows:
            for w in qs:
                if w[:1].isdigit():
                    continue
                df = sum(1 for r in kand_rows if soft_has(r["стемы"], w))
                if df / len(kand_rows) < 0.5:
                    utochn.add(w)

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
            # уточняющие слова-подтипы: буст за совпадение (мягкое, по префиксу)
            for w in utochn:
                if soft_has(row["стемы"], w):
                    s += 2.5
            # назначение по материалу (этап 7): «сверло по дереву» → «Сверло по дер.»
            if zapros_materialy:
                rm = row["материалы"]
                if rm & zapros_materialy:
                    s += 4.0           # верный материал в имени
                elif rm:
                    s -= 2.0           # конфликт: в имени другой материал
            # рулевой словарь (этап 6): материал -> нужная подгруппа Форы
            if mat_subs and row["t"].get("podgruppa", "") in mat_subs:
                s += 4.0
            # рулевой словарь: сленг-слово -> токен в имени (филипс->PH, американка->амер)
            if name_tokens:
                imya_low = row["t"].get("imya", "").lower()
                for tok in name_tokens:
                    if tok in imya_low:
                        s += 3.0
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
        self._last_top = scored[0][0] if scored else 0.0

        kanal = "+".join(c for c in (
            "подгр" if (use_podgr and subs) else "",
            "словарь" if semi else "",
            "атрибуты",
        ) if c)
        return [t for _, t in scored[:top]], kanal
