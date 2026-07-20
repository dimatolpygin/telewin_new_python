# -*- coding: utf-8 -*-
"""Минимальный async-клиент OpenRouter (OpenAI-совместимый chat/completions).
Порт openrouter.ts на httpx.
"""
import asyncio

import httpx

from ..config import OpenRouterConfig


class OpenRouterError(Exception):
    def __init__(self, message: str, retry: bool):
        super().__init__(message)
        self.retry = retry


async def chat(cfg: OpenRouterConfig, messages: list[dict], tools: list[dict] | None = None) -> dict:
    """Возвращает message ассистента: {'content': str|None, 'tool_calls': [...]|None}.

    До 3 попыток: сетевой сбой и 429/5xx — временные, повторяем с ростом паузы.
    4xx (кроме 429) — ошибка запроса, повтор бесполезен.
    """
    body: dict = {"model": cfg.model, "messages": messages, "temperature": 0.2}
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {cfg.api_key}",
        "HTTP-Referer": "https://masterberez.ru",
        "X-Title": "telewin-test",
    }

    MAX = 3
    last: Exception | None = None
    async with httpx.AsyncClient(timeout=60.0) as client:
        for popytka in range(1, MAX + 1):
            try:
                res = await client.post(
                    f"{cfg.base_url}/chat/completions", json=body, headers=headers
                )
                text = res.text
                if res.status_code == 429 or res.status_code >= 500:
                    raise OpenRouterError(
                        f"OpenRouter {res.status_code} (временная): {text[:200]}", retry=True
                    )
                if res.status_code >= 400:
                    raise OpenRouterError(f"OpenRouter {res.status_code}: {text[:500]}", retry=False)
                data = res.json()
                msg = (data.get("choices") or [{}])[0].get("message")
                if not msg:
                    raise OpenRouterError(f"OpenRouter: пустой ответ {text[:300]}", retry=True)
                return {"content": msg.get("content"), "tool_calls": msg.get("tool_calls")}
            except OpenRouterError as e:
                last = e
                if not e.retry or popytka == MAX:
                    raise
                await asyncio.sleep(0.8 * popytka)
            except (httpx.TransportError, httpx.TimeoutException) as e:
                last = e
                if popytka == MAX:
                    raise
                await asyncio.sleep(0.8 * popytka)
    assert last is not None
    raise last
