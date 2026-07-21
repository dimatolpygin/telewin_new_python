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
from .fuzzy import word_sim

_DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data")

# Цветовые температуры ламп (этап 14): 4-значные, чтобы не путать с артикулом при
# лампа-контексте («лампа тёплый свет 2700» ≠ товар с артикулом 2700).
_TEMP_ARTIKUL_GUARD = {"2700", "3000", "3500", "4000", "4500", "5000", "5700", "6000", "6400", "6500"}


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
            sem = t.get("semeystvo") or family_of(imya)
            self.rows.append({
                "t": t,
                "семейство": sem,
                "сем_стемы": stems(sem),   # стемы имени семейства (тай-брейк точного совпадения)
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

        # бренды (этап 15): страны/заглушки в поле proizvoditel — НЕ бренд; народные
        # алиасы (кириллица) известных марок инструмента для честной пометки/буста.
        brendy = json.load(open(os.path.join(data_dir, "brendy.json"), encoding="utf-8"))
        self.ne_brend = {s.strip().lower() for s in brendy.get("не_бренд", [])}
        self.narodnye_aliasy = {k.lower(): v for k, v in brendy.get("народные_алиасы", {}).items()}

        # родовой канал действий/назначения (этап 16): стем глагола -> семейства-кандидаты
        # («чем красить» -> Краска/Эмаль/Валик/Кисть). Только служебные ключи с "_" отсеиваем.
        rodo = json.load(open(os.path.join(data_dir, "rodovye.json"), encoding="utf-8"))
        self.rodovye = {k: v for k, v in rodo.items() if not k.startswith("_")}

        # индекс канала производителя: производитель -> его стемы и его строки.
        # Страны/заглушки («Россия»/«Китай»/«Без бренда»/«ноль») исключаем из стем-индекса —
        # иначе «россия» уводит канал производителя в 4411 товаров (баг этапа 15).
        self.proizv_stems = {}
        self.po_proizv = collections.defaultdict(list)
        for row in self.rows:
            pr = row["t"].get("proizvoditel", "")
            self.po_proizv[pr].append(row)
            if pr not in self.proizv_stems and pr.strip().lower() not in self.ne_brend:
                self.proizv_stems[pr] = stems(pr)
        # множество всех производителей в нижнем регистре (для сопоставления бренда из запроса)
        self._proizv_low = {r["t"].get("proizvoditel", "").lower() for r in self.rows}

        # варианты названия семейства: само имя, канон, каждый синоним
        self.varianty = {}
        self.slovar_sem = {}
        self.fam_key_stems = {}    # стемы ИМЕНИ семейства (главный сигнал фаззи)
        self.fam_name_stems = {}   # стемы ИМЕНИ+канона семейства (для фаззи по опечатке)
        for f, e in self.slovar.items():
            vs = [f, e.get("канон", "")] + list(e.get("синонимы", []))
            self.varianty[f] = [stems(v) for v in vs if v and stems(v)]
            all_st = set()
            for v in self.varianty[f]:
                all_st |= v
            self.slovar_sem[f] = all_st
            self.fam_key_stems[f] = stems(f)
            self.fam_name_stems[f] = stems(f) | stems(e.get("канон", ""))

        # словарь известных стемов (этап 8): всё, что встречается в именах товаров
        # или в словаре домена. Слово запроса ВНЕ него — кандидат в опечатки (фаззи-канал).
        self.vocab = set()
        for ss in self.slovar_sem.values():
            self.vocab |= ss
        for row in self.rows:
            self.vocab |= row["стемы"]

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
        # цоколь лампы (этап 14): формы/сленг («миньон»→E14) поверх прямого разбора
        # razobrat. Матчим по q.lower(), НЕ по qn: norm-гомоглиф латинскую x→кир х
        # («gx53»→«gх53») и латинские коды цоколя ломались бы.
        ql = q.lower()
        for slovo, c in self.sleng.get("цоколь", {}).items():
            if re.search(rf"\b{re.escape(slovo)}\b", ql):
                a.setdefault("цоколь", c)
        # цветовая температура (этап 14): слово тёплый/холодный/нейтральный → К
        for slovo, t in self.sleng.get("цв_температура", {}).items():
            if re.search(rf"\b{re.escape(slovo)}\b", qn):
                a.setdefault("цв_температура", int(t))
        a["_числа"] = {float(x.replace(",", ".")) for x in re.findall(r"\d+(?:[.,]\d+)?", qn)}
        return a

    _DEP_PREP = {"для", "под"}

    def head_dep(self, q: str):
        """Головное слово vs зависимое (этап 9). «муфта ДЛЯ трубы ПП» → head=муфта
        (что ищем), dep=труба/пп/20 (с чем совместимо). Возвращает (head_стемы,
        dep_стемы) или (None, None), если структуры «X для/под Y» нет."""
        toks = re.findall(r"[а-я0-9][а-я0-9.,/]*", norm(q))
        idx = next((i for i, t in enumerate(toks) if t.strip(".,") in self._DEP_PREP), None)
        if not idx:  # None или 0 (запрос начинается с предлога) — структуры нет
            return None, None
        hs, ds = set(), set()
        for t in toks[:idx]:
            hs |= stems(t)
        for t in toks[idx + 1:]:
            ds |= stems(t)
        return hs, ds

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

    def fuzzy_semeystva(self, qs: set, porog: float = 0.8):
        """Фаззи-канал опечаток (этап 8): слово запроса ВНЕ словаря стенда (OOV)
        сопоставляется по сходству слов с вариантами семейств словаря. Возвращает
        [(сходство, семейство, слово)] для срабатываний ≥ porog. Жёсткие каналы
        (штрихкод/артикул) отрабатывают раньше — фаззи их не трогает.

        OOV-гард: реальные слова из имён товаров («труба», «кнопочный») в vocab и
        не триггерят, поэтому «трубка»↔«труба» не путаются — путаются лишь опечатки."""
        res = []
        for w in qs:
            if len(w) < 5 or w[:1].isdigit() or w in self.vocab:
                continue
            # Сопоставляем с ИМЕНЕМ/каноном семейства. Совпадение по имени-ключу
            # весит выше, чем по канону/синониму: «выключетель» → Выключатель
            # (имя), а не «Блок» (у Блока «выключатель» лишь в каноне). Инжектим
            # все семейства ≥ порога — уточняющие слова выберут верное.
            for f, ns in self.fam_name_stems.items():
                name_sim = max((word_sim(w, v) for v in ns if len(v) >= 5), default=0.0)
                if name_sim < porog:
                    continue
                key_sim = max((word_sim(w, v) for v in self.fam_key_stems[f] if len(v) >= 5), default=0.0)
                eff = key_sim if key_sim >= porog else name_sim - 0.15
                res.append((eff, f, w))
        return res

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
        Полное покрытие + токен ≥3 симв — чтобы не ловить короткие/общие слова.
        Страны/заглушки уже исключены из `proizv_stems` (этап 15)."""
        res = []
        for pr, ss in self.proizv_stems.items():
            if not ss or not pr:
                continue
            if ss <= qs and any(len(w) >= 3 for w in ss):
                res.append(pr)
        return res

    def brend_iz_zaprosa(self, q: str, qs: set | None = None):
        """Бренд, названный в запросе (этап 15). Возвращает (отображаемое_имя | None,
        множество производителей-в-нижнем-регистре, совпавших с этим брендом).
        Пустое множество при непустом имени = бренд назван, но такого в прайсе нет →
        основание для честной пометки «именно этого бренда нет, есть аналоги»."""
        if qs is None:
            qs = stems(q)
        ql = q.lower()
        # 1) народные марки инструмента по алиасу (кириллица/латиница), по границе слова —
        #    в прайсе бренд хранится латиницей (MAKITA), стем-канал кириллицу не ловит.
        for alias, disp in self.narodnye_aliasy.items():
            if re.search(r"(?<![а-яёa-z])" + re.escape(alias) + r"(?![а-яёa-z])", ql):
                dl = disp.lower()
                return disp, {pr for pr in self._proizv_low if dl in pr}
        # 2) реальный производитель прайса, полностью названный в запросе (без заглушек)
        prs = self.proizvoditeli_po_zaprosu(qs)
        if prs:
            return prs[0], {prs[0].lower()}
        return None, set()

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
        # головное vs зависимое слово (этап 9): «муфта для трубы» → head=муфта
        head_st, dep_st = self.head_dep(q)
        # бренд, названный в запросе (этап 15): буст его товаров внутри семейства
        # («болгарка вихрь» → УШМ Вихрь, а не Ресанта). brend_match — производители-в-нижнем.
        _brend_disp, brend_match = self.brend_iz_zaprosa(q, qs)
        self._last_top = 0.0  # диагностика: балл лучшего кандидата (для калибровки порога)

        # прямой канал: штрихкод (EAN, 8-13 цифр) — самый специфичный
        for m in re.findall(r"\b\d{8,13}\b", q):
            hit = [r["t"] for r in self.rows if r["t"].get("shtrihkod") == m]
            if hit:
                return hit[:top], "штрихкод"
        # прямой канал: артикул (4-6 цифр). Но цветовая температура лампы (2700/4000/
        # 6500…) — тоже 4 цифры: при лампа-контексте НЕ трактуем её как артикул
        # (иначе «лампа тёплый свет 2700» короткозамыкает на товар с артикулом 2700).
        lamp_ctx = bool(re.search(r"ламп|свет|цокол|тепл|холодн|нейтральн", norm(q)))
        for m in re.findall(r"\b\d{4,6}\b", q):
            if lamp_ctx and m in _TEMP_ARTIKUL_GUARD:
                continue
            hit = [r["t"] for r in self.rows
                   if r["t"].get("artikul") == m or str(r["t"].get("artikul", "")).lstrip("0") == m.lstrip("0")]
            if hit:
                return hit[:top], "артикул"

        sem_score = {}
        semi = []
        fuzzy_fams = set()
        fuzzy_words = set()   # OOV-слова-опечатки: не участвуют в utochn (чтобы «выкл»-префикс не бустил соседа)
        if use_slovar:
            kand = self.semeystva_kandidaty(qs)
            sem_score = {f: s for s, f in kand}
            if kand and kand[0][0] >= 0.6:
                porog = kand[0][0] - 0.35
                semi = [f for s, f in kand if s >= porog][:4]
                # при названном бренде семейство дробится по модели («УШМ-125/» vs
                # «УШМ-180/…»), и товар бренда может не попасть в топ-4 равнобалльных
                # семейств. Целево дотягиваем семейства-кандидаты (в пределах porog),
                # где реально есть товар бренда, — чтобы буст его достал (этап 15).
                if brend_match:
                    for s, f in kand:
                        if s >= porog and f not in semi and any(
                            r["t"].get("proizvoditel", "").lower() in brend_match
                            for r in self.po_sem.get(f, [])):
                            semi.append(f)
            # фаззи-канал опечаток: OOV-слово запроса -> семейство по сходству.
            # Fusion: добавляет семейство-кандидата, не заменяя лексику; жёсткие
            # каналы (штрихкод/артикул) уже отработали выше.
            for si, f, w in self.fuzzy_semeystva(qs):
                sem_score[f] = max(sem_score.get(f, 0.0), si)
                fuzzy_fams.add(f)
                fuzzy_words.add(w)
                if f not in semi:
                    semi.append(f)
            # родовой канал действий/назначения (этап 16): стем-глагол запроса →
            # семейства-кандидаты («чем красить» → Краска/Эмаль/Валик/Кисть). Мягкая
            # инъекция (вес 1.0): конкурирует в скоринге, не короткозамыкает. Даёт ответ
            # там, где стем глагола ≠ стему семейства (крас≠краск) и словарь молчал.
            for w in qs:
                for f in self.rodovye.get(w, []):
                    if f in self.po_sem:
                        sem_score[f] = max(sem_score.get(f, 0.0), 1.0)
                        if f not in semi:
                            semi.append(f)

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
                if w[:1].isdigit() or w in fuzzy_words:
                    continue  # слово-опечатку не используем как уточняющее (его сигнал — в фаззи-балле)
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
            # рулевой словарь: сленг-слово -> токен-подтип в имени (филипс->PH,
            # американка->амер, гипсокартон->ШСГД, масляная->ПФ-115). Осознанно
            # названный подтип — сильный сигнал, должен доминировать над общими
            # словами (этап 13: «масляная краска» → Эмаль ПФ-115, не водоэмульсионка).
            if name_tokens:
                imya_low = row["t"].get("imya", "").lower()
                for tok in name_tokens:
                    if tok in imya_low:
                        s += 4.0
            # бренд из запроса (этап 15): товар нужного бренда внутри семейства — буст.
            # «болгарка вихрь» → УШМ Вихрь поверх УШМ Ресанта. Не короткозамыкает: работает
            # только внутри уже отобранных семейств-кандидатов, как тай-брейк подтипа.
            if brend_match and row["t"].get("proizvoditel", "").lower() in brend_match:
                s += 3.0
            # пересечение слов запроса с именем товара
            s += 1.2 * len(qs & row["стемы"])
            # тай-брейк: имя семейства полностью названо запросом, без лишних слов
            # («ножовка» → «Ножовка», а не «Ножовка-ручка»)
            if row["сем_стемы"] and row["сем_стемы"] <= qs:
                s += 0.4
            # головное слово (этап 9): семейство = head запроса — буст; семейство =
            # лишь зависимое («для трубы») — штраф. «муфта для трубы»→Муфта, не Труба.
            if head_st is not None:
                fam = row["сем_стемы"]
                if fam & head_st:
                    s += 2.5
                elif fam and dep_st and fam <= dep_st:
                    s -= 2.5
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

        # победитель пришёл из фаззи-семейства? (для диагностики канала)
        top_t = scored[0][1] if scored else None
        top_fam = (top_t.get("semeystvo") or family_of(top_t.get("imya", ""))) if top_t else None
        cherez_fuzzy = top_fam in fuzzy_fams

        kanal = "+".join(c for c in (
            "подгр" if (use_podgr and subs) else "",
            "фаззи" if cherez_fuzzy else "",
            "словарь" if (semi and not cherez_fuzzy) else "",
            "атрибуты",
        ) if c)
        return [t for _, t in scored[:top]], kanal
