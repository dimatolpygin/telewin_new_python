# -*- coding: utf-8 -*-
"""Оркестрация диалога: ИИ понимает запрос, при нужде уточняет, вызывает
детерминированный поиск, обрамляет найденный факт. Числа ИИ не генерирует.
Порт agent.ts.
"""
import json
from dataclasses import dataclass, field

from ..config import OpenRouterConfig
from ..search.search import Poisk
from ..logger import logger
from .openrouter import chat

ДАТА_ПРАЙСА = "18.07.2026"

SYSTEM = f"""Ты — консультант сети хозяйственных магазинов «Домашний мастер» (masterberez.ru).
Две точки: Микрорайон и Берёзовская. Помогаешь покупателю подобрать товар по прайсу и назвать цену и наличие.

Ассортимент: инструмент ручной, электротовары, сантехника, крепёж, строительно-отделочные материалы,
замочно-скобяные изделия, садовый инвентарь, товары для дома, расходники, средства от вредителей, удобрения.
Мы НЕ продаём: крупную бытовую технику (холодильники, микроволновки, кондиционеры, стиральные машины,
обогреватели-сплит), аудио/видео-электронику, мебель, одежду. Если просят такое — сразу скажи, что этого
в ассортименте нет, и не вызывай поиск.

КАК РАБОТАТЬ:
1. Покупатель пишет на обычном языке, часто без точных названий: «гвозди сотка», «саморезы для гипсокартона»,
   «чем прикрутить профлист». Твоя задача — понять, что ему нужно.
2. Если для точного подбора не хватает важной детали (тип, размер, материал) — задай 1–2 КОРОТКИХ уточняющих
   вопроса и НЕ вызывай поиск, пока не прояснится. Пример: на «нужны саморезы» уточни «по дереву или по металлу?
   какой длины?». Не задавай лишних вопросов, если и так понятно.
3. Когда ясно — вызови инструмент search_products с ключевыми словами товара.
4. Получив результат поиска, СНАЧАЛА проверь каждую позицию: реально ли она соответствует тому, что просил
   покупатель (тип и назначение). Поиск возвращает «наиболее похожие» кандидаты и МОЖЕТ ошибаться —
   зацепиться за одно общее слово. Пример: на «микроволновку» поиск может вернуть «Печь-набор кружков»,
   на «кондиционер» — «Систему выравнивания плитки»; это НЕ то, подавать их как ответ нельзя.
5. Называй покупателю только реально подходящие позиции: наименование, цену, наличие по двум точкам.
   Если позиций несколько похожих — предложи выбор коротким списком.
6. Если поиск вернул пусто ИЛИ ни одна позиция по смыслу не подходит — честно скажи, что такого не нашёл,
   попроси уточнить или переформулировать. Никогда не выдавай неподходящий товар за ответ. Не выдумывай.

ЖЁСТКИЕ ПРАВИЛА:
- Цену и остатки бери ТОЛЬКО из результата инструмента. Никогда не придумывай числа.
- Всегда добавляй, что данные актуальны на {ДАТА_ПРАЙСА}.
- Пиши по-русски, кратко и по-деловому. Без эмодзи.
- Остатки не суммируй сам — называй как есть по каждой точке."""

TOOLS = [{
    "type": "function",
    "function": {
        "name": "search_products",
        "description": (
            "Поиск товара в прайсе магазина по ключевым словам. Передавай тип товара и характеристики "
            "нормальными словами и цифрами, например: «гвозди строительные 100», «саморез гипсокартон дерево 3.5 45», "
            "«смеситель кухня», «лампа E27 60вт». Возвращает до 5 наиболее подходящих позиций с ценой и остатками."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Ключевые слова товара для поиска (тип + размер/характеристики).",
                },
            },
            "required": ["query"],
        },
    },
}]


@dataclass
class AgentResult:
    answer: str
    new_history: list[dict]
    zaprosy_poiska: list[str] = field(default_factory=list)
    naydeno: int = 0


async def run_agent(
    cfg: OpenRouterConfig, poisk: Poisk, history: list[dict], user_text: str,
    use_podgr: bool = True,
) -> AgentResult:
    messages: list[dict] = [{"role": "system", "content": SYSTEM}, *history,
                            {"role": "user", "content": user_text}]
    zaprosy_poiska: list[str] = []
    poslednee_naydeno = 0

    for _ in range(3):
        res = await chat(cfg, messages, TOOLS)

        if res.get("tool_calls"):
            messages.append({"role": "assistant", "content": res.get("content") or "",
                             "tool_calls": res["tool_calls"]})
            for tc in res["tool_calls"]:
                try:
                    query = json.loads(tc["function"]["arguments"]).get("query", "")
                except (json.JSONDecodeError, KeyError, TypeError):
                    query = ""
                zaprosy_poiska.append(query)
                rezultaty, kanal = poisk.iskat(query, use_podgr=use_podgr)
                poslednee_naydeno = len(rezultaty)
                payload = {
                    "дата_актуальности": ДАТА_ПРАЙСА,
                    "канал_поиска": kanal,
                    "найдено": [{
                        "наименование": r.get("imya"),
                        "цена": r.get("cena"),
                        "единица": r.get("edinica"),
                        "остаток_всего": r.get("ostatok_obshiy"),
                        "остаток_микрорайон": r.get("ostatok_mikro"),
                        "остаток_березовская": r.get("ostatok_berez"),
                        "артикул": r.get("artikul"),
                    } for r in rezultaty],
                }
                messages.append({"role": "tool", "tool_call_id": tc["id"],
                                 "content": json.dumps(payload, ensure_ascii=False)})
            continue  # даём модели обработать результат

        answer = (res.get("content") or "").strip() or "Извините, не понял запрос. Уточните, что нужно?"
        new_history = [*history, {"role": "user", "content": user_text},
                       {"role": "assistant", "content": answer}]
        return AgentResult(answer, new_history, zaprosy_poiska, poslednee_naydeno)

    logger.warning("Агент исчерпал лимит итераций")
    answer = "Уточните запрос, пожалуйста — не смог подобрать товар."
    return AgentResult(
        answer,
        [*history, {"role": "user", "content": user_text}, {"role": "assistant", "content": answer}],
        zaprosy_poiska, poslednee_naydeno,
    )
