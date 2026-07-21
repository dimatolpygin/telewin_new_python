# -*- coding: utf-8 -*-
"""Гибридный поиск (этап 10): лексика (словарь+атрибуты+фаззи) ⊕ вектор, слитые
через RRF (Reciprocal Rank Fusion).

Принципы:
  · жёсткие/прямые каналы (штрихкод, артикул, производитель) — приоритет; вектор в них
    НЕ вмешивается (их результат возвращается как есть);
  · когда лексика нашла кандидатов — вектор до-ранжирует и добавляет recall, но не
    затирает точные совпадения (лексический ранг участвует в RRF наравне);
  · когда лексика пуста (слова нет в словаре: «болгарка», «наждачка») — работает только
    вектор, но с порогом похожести: иначе на мусорный запрос kNN всегда что-то вернёт,
    а абстейн (этап 6) держится на этом. Порог обходит лишь осмысленные запросы.

RRF: score(d) = Σ_канал  w_канал / (K + rank_канал(d)).  K сглаживает вклад хвоста.
"""
import json
import os
import re
from collections import defaultdict

from .search import Poisk
from .normalize import norm
from .vector import VectorKanal

_DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data")

# Параметры слияния. Подобраны свипом по кэшу каналов на золотом+слепом наборах
# (docs/ZAMER_GIBRID.md): при W_VEC>0.3 золотой набор регрессирует 38→37 (вектор
# перебивает точный лексический матч, напр. «саморез черный 45»→ШУЦ), поэтому вес
# вектора умеренный. Порог 0.76 восстанавливает абстейн на мусоре (иначе kNN всегда
# что-то возвращает) и при этом не режет товарные запросы (их top-1 от порога не зависит).
K_RRF = 60            # классическая константа RRF
W_LEX = 1.0           # вес лексического ранга (точные размеры/атрибуты — здесь)
W_VEC = 0.3           # вес векторного ранга (recall по синонимам/композиции)
ГЛУБИНА = 200         # сколько кандидатов берём из каждого канала для слияния
ПОРОГ_ВЕКТОРА = 0.76  # мин. косинус-похожесть, чтобы вернуть чисто-векторный ответ

_ЖЁСТКИЕ = {"штрихкод", "артикул", "производитель"}


class Gibrid:
    """Обёртка над лексическим `Poisk` + векторным каналом. Хранит индекс id→товар
    для связывания каналов (товары загружены из БД, у каждого есть `id`)."""

    def __init__(self, poisk: Poisk, vk: VectorKanal, data_dir: str | None = None):
        self.poisk = poisk
        self.vk = vk
        self.по_id = {}
        for row in poisk.rows:
            i = row["t"].get("id")
            if i is not None:
                self.по_id[i] = row["t"]
        # словарь чужого домена (этап 12, абстейн-гейт)
        cd = json.load(open(os.path.join(data_dir or _DATA, "chuzhoy_domen.json"), encoding="utf-8"))
        self._chuzhoy_kval = [k.lower() for k in cd.get("квалификаторы", [])]
        self._chuzhoy_tov = [re.compile(rf"\b{re.escape(t.strip())}") for t in cd.get("чужие_товары", [])]

    def _chuzhoy_domen(self, q: str) -> str | None:
        """Гейт абстейна (этап 12): запрос из ЧУЖОГО домена (косметика/еда/техника/
        одежда/зоо) → маркер. Квалификаторы («для волос», «зубная») ловятся подстрокой,
        чужие товары («телевизор») — по границе слова. Реальный ассортимент (мыло,
        белизна, масло пихтовое, репеллент) маркеров не содержит и не гейтится."""
        qn = norm(q)
        for k in self._chuzhoy_kval:
            if k in qn:
                return k
        for rx in self._chuzhoy_tov:
            if rx.search(qn):
                return rx.pattern
        return None

    async def iskat(self, q: str, top: int = 5, use_podgr: bool = True,
                    use_slovar: bool = True, use_proizv: bool = True):
        """Возвращает (список_товаров, канал) — как `Poisk.iskat`, но с векторным слиянием."""
        # абстейн-гейт (этап 12): чужой домен → «не найдено» ДО поиска (не выдумываем
        # близкий товар и не тратим векторный вызов). Утечки шли и через лексику, поэтому
        # гейт стоит перед всеми каналами.
        if self._chuzhoy_domen(q):
            return [], "чужой домен"
        lex, kanal = self.poisk.iskat(
            q, top=ГЛУБИНА, use_podgr=use_podgr, use_slovar=use_slovar, use_proizv=use_proizv
        )
        # прямые/жёсткие каналы — вектор не трогаем
        if kanal in _ЖЁСТКИЕ:
            return lex[:top], kanal

        vec = await self.vk.knn(q, limit=ГЛУБИНА)  # [(id, sim)]

        # чисто-векторный ответ (лексика пуста): порог отсекает мусор
        if not lex:
            if not vec or vec[0][1] < ПОРОГ_ВЕКТОРА:
                return [], "не найдено"
            товары = [self.по_id[i] for i, _ in vec if i in self.по_id][:top]
            return товары, "вектор"

        # RRF-слияние лексики и вектора
        rrf: dict = defaultdict(float)
        for rank, t in enumerate(lex):
            i = t.get("id")
            if i is not None:
                rrf[i] += W_LEX / (K_RRF + rank + 1)
        for rank, (i, _sim) in enumerate(vec):
            rrf[i] += W_VEC / (K_RRF + rank + 1)

        порядок = sorted(rrf, key=lambda i: -rrf[i])
        товары = [self.по_id[i] for i in порядок if i in self.по_id][:top]
        # помечаем, что вектор участвовал (для логов/диагностики)
        return товары, (kanal + "+вектор" if kanal and kanal != "не найдено" else "вектор")
