# -*- coding: utf-8 -*-
"""Baseline Milestone 2 (этап 11): мерная линейка по 5 сегментам через ГИБРИД.

Сегменты:
  1. Абстейн        — мусор слепого набора (прием=мусор) + `abstain_nabor.json`.
                      Метрика: абстейн-точность = доля запросов, где гибрид вернул ПУСТО.
  2. Разговорный    — слепой набор, прием ∈ {сленг, опечатка}. Метрика: top-1 / hit@5.
  3. Родовой        — слепой набор, прием = родовое. Метрика: top-1 / hit@5.
  4. Размер (слепой)— слепой набор, прием = размер. Метрика: top-1 / hit@5.
  5. Размерный точн.— `razmer_nabor.json` (новый). Метрика: top-1 / hit@5 +
                      размерная точность (top-1 совпал с «ждём» и НЕ с «анти»-размером).
Контроль: золотой набор top-1 (инвариант ≥95%, линейка не должна его менять).

Метрики честно снимаются на боевом пути (гибрид, порог вектора 0.76). Требует
посчитанные эмбеддинги (`python -m bot.embed_index --all`) и поднятый pgvector :5434.

Запуск: python -m bot.search.baseline_v2 [--save]
"""
import asyncio
import json
import os
import re
import sys

import asyncpg

from ..config import load_config
from ..db import zagruzit_vse_tovary
from .search import Poisk
from .vector import VectorKanal
from .gibrid import Gibrid

_DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data")


def _load(name: str) -> list[dict]:
    return json.load(open(os.path.join(_DATA, name), encoding="utf-8"))


def _priem(r: dict) -> str:
    return r.get("прием") or r.get("приём") or "—"


def _имя(t) -> str:
    return t.get("imya", "") if isinstance(t, dict) else ""


async def _tovar_metriki(кейсы, гибр_fn) -> dict:
    """top-1 / hit@5 по «ждём»-регэкспу для товарных кейсов."""
    n = top1 = hit5 = 0
    провалы = []
    for k in кейсы:
        pat = k["ждём"]
        rx = re.compile(pat, re.I)
        res, _ = await гибр_fn(k["запрос"])
        n += 1
        t1 = bool(res) and bool(rx.search(_имя(res[0])))
        h5 = any(rx.search(_имя(t)) for t in res[:5])
        top1 += t1
        hit5 += h5
        if not t1:
            провалы.append((k["запрос"], _имя(res[0]) if res else "ПУСТО"))
    return {"n": n, "top1": top1, "hit5": hit5, "провалы": провалы}


async def _abstain_metriki(кейсы, гибр_fn) -> dict:
    """Абстейн-точность = доля, где гибрид вернул ПУСТО (не выдумал товар)."""
    n = ok = 0
    утечки = []
    for k in кейсы:
        res, kanal = await гибр_fn(k["запрос"])
        n += 1
        if not res:
            ok += 1
        else:
            утечки.append((k["запрос"], _имя(res[0]), kanal))
    return {"n": n, "ok": ok, "утечки": утечки}


async def _razmer_metriki(кейсы, гибр_fn) -> dict:
    """top-1 / hit@5 + размерная точность (top-1 = ждём и НЕ анти)."""
    n = top1 = hit5 = точн = 0
    промахи = []
    for k in кейсы:
        rx = re.compile(k["ждём"], re.I)
        anti = re.compile(k["анти"], re.I) if k.get("анти") else None
        res, _ = await гибр_fn(k["запрос"])
        n += 1
        im1 = _имя(res[0]) if res else ""
        t1 = bool(res) and bool(rx.search(im1))
        h5 = any(rx.search(_имя(t)) for t in res[:5])
        # размерно точен: top-1 верного размера и не конфузного
        точ = t1 and not (anti and anti.search(im1))
        top1 += t1
        hit5 += h5
        точн += точ
        if not точ:
            промахи.append((k["запрос"], im1 or "ПУСТО"))
    return {"n": n, "top1": top1, "hit5": hit5, "точн": точн, "промахи": промахи}


def _pct(a: int, b: int) -> str:
    return f"{a/b*100:.0f}%" if b else "—"


async def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    cfg = load_config()
    pool = await asyncpg.create_pool(
        host=cfg.pg.host, port=cfg.pg.port, user=cfg.pg.user,
        password=cfg.pg.password, database=cfg.pg.database, min_size=1, max_size=4,
    )
    try:
        tovary = await zagruzit_vse_tovary(pool, cfg.pg.schema)
        poisk = Poisk(tovary)
        vk = VectorKanal(pool, cfg.pg.schema)
        if not await vk.доступен():
            print("Эмбеддинги не посчитаны — сначала `python -m bot.embed_index --all`.")
            return
        gibrid = Gibrid(poisk, vk)
        имена = [t["imya"] for t in tovary]

        async def гибр_fn(q):
            return await gibrid.iskat(q, top=5, use_podgr=True)

        слепой = _load("test_nabor.json")
        золото = _load("zolotoy_nabor.json")
        размер_нов = _load("razmer_nabor.json")
        абстейн_нов = _load("abstain_nabor.json")

        # сегментация слепого по «прием»
        мусор_слеп = [r for r in слепой if _priem(r) == "мусор"]
        разговор = [r for r in слепой if _priem(r) in ("сленг", "опечатка")]
        родовой = [r for r in слепой if _priem(r) == "родовое"]
        размер_слеп = [r for r in слепой if _priem(r) == "размер"]

        # прогон
        seg_абстейн = await _abstain_metriki(мусор_слеп + абстейн_нов, гибр_fn)
        seg_разговор = await _tovar_metriki(разговор, гибр_fn)
        seg_родовой = await _tovar_metriki(родовой, гибр_fn)
        seg_размер_слеп = await _tovar_metriki(размер_слеп, гибр_fn)
        seg_размер_нов = await _razmer_metriki(размер_нов, гибр_fn)

        # контроль: золото (top-1 по «ждём», товары есть в файле)
        зол_кейсы = [r for r in золото if r.get("ждём") and any(re.search(r["ждём"], n, re.I) for n in имена)]
        seg_золото = await _tovar_metriki(зол_кейсы, гибр_fn)

        out = []
        out.append("# BASELINE v2 — Milestone 2 (этап 11), гибрид, порог вектора 0.76")
        out.append("")
        out.append(f"Снято: гибрид (лексика ⊕ вектор RRF), top=5. Наборы: слепой {len(слепой)}, "
                   f"золотой {len(золото)}, новый размерный {len(размер_нов)}, новый абстейн {len(абстейн_нов)}.")
        out.append("")
        out.append("## Сегменты")
        out.append("")
        out.append("| # | Сегмент | n | top-1 | hit@5 | абстейн-точн. | размерная точн. |")
        out.append("|---|---|---|---|---|---|---|")
        a = seg_абстейн
        out.append(f"| 1 | Абстейн (мусор+новый) | {a['n']} | — | — | **{_pct(a['ok'], a['n'])}** ({a['ok']}/{a['n']}) | — |")
        for i, (name, s) in enumerate([
            ("Разговорный (сленг+опечатка)", seg_разговор),
            ("Родовой", seg_родовой),
            ("Размер (слепой)", seg_размер_слеп),
        ], start=2):
            out.append(f"| {i} | {name} | {s['n']} | {_pct(s['top1'], s['n'])} ({s['top1']}/{s['n']}) | {_pct(s['hit5'], s['n'])} | — | — |")
        r = seg_размер_нов
        out.append(f"| 5 | Размерный точный (новый) | {r['n']} | {_pct(r['top1'], r['n'])} ({r['top1']}/{r['n']}) | {_pct(r['hit5'], r['n'])} | — | **{_pct(r['точн'], r['n'])}** ({r['точн']}/{r['n']}) |")
        out.append("")
        z = seg_золото
        out.append(f"**Контроль — золотой набор**: top-1 {_pct(z['top1'], z['n'])} ({z['top1']}/{z['n']}), "
                   f"hit@5 {_pct(z['hit5'], z['n'])}. Инвариант ≥95% — линейка код поиска не меняла.")
        out.append("")
        out.append("## Цели Milestone 2 по сегментам (baseline → цель)")
        out.append("")
        out.append(f"- Абстейн: {_pct(a['ok'], a['n'])} → ≥90% (этап 12, риск №1)")
        out.append(f"- Разговорный: {_pct(seg_разговор['top1'], seg_разговор['n'])} → ≥82% (этап 13)")
        out.append(f"- Размерный точный: точн. {_pct(r['точн'], r['n'])} → ≥90% (этап 14)")
        out.append(f"- Родовой: {_pct(seg_родовой['top1'], seg_родовой['n'])} → ≥78% (этап 16)")
        out.append("")
        out.append("## Утечки абстейна (гибрид выдумал товар на чужой запрос) — материал этапа 12")
        out.append("")
        for q, im, kan in a["утечки"][:30]:
            out.append(f"- «{q}» → {im}  _(канал: {kan})_")
        out.append("")
        out.append("## Промахи размерной точности — материал этапа 14")
        out.append("")
        for q, im in r["промахи"][:32]:
            out.append(f"- «{q}» → {im}")

        текст = "\n".join(out)
        print(текст)
        if "--save" in sys.argv:
            with open(os.path.join(os.path.dirname(_DATA), "docs", "BASELINE_V2.md"), "w", encoding="utf-8") as f:
                f.write(текст + "\n")
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
