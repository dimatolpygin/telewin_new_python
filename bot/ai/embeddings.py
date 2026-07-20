# -*- coding: utf-8 -*-
"""Эмбеддинги через OpenRouter (`POST /api/v1/embeddings`). Порт эмбеддинги.ts.

Модель по умолчанию — `google/gemini-embedding-001` (3072 мерности): выбрана замером
на боевых именах прайса в основном проекте (проходит «гвозди сотка» и «крепёж», где
text-embedding-3 и bge-m3 ставили клей/мусор выше цели). Детерминирована.

Конфиг читаем лениво из env (а не из общего config.py): CLI, которым эмбеддинги не нужны
(`import`, `probe`), не должны падать на старте из-за отсутствия ключа.
"""
import asyncio
import os

import httpx

# Имена товаров короткие, но не жадничаем — 64 текста в запрос.
РАЗМЕР_БАТЧА = 64
MAX_ПОПЫТОК = 3


class EmbeddingError(Exception):
    def __init__(self, message: str, retry: bool):
        super().__init__(message)
        self.retry = retry


def _настройки() -> dict:
    ключ = (os.environ.get("OPENROUTER_API_KEY") or "").strip()
    if not ключ:
        raise EmbeddingError(
            "Не задан OPENROUTER_API_KEY — он нужен для эмбеддингов (этап 10). "
            "Заполните .env (см. .env.example).",
            retry=False,
        )
    return {
        "ключ": ключ,
        "база": (os.environ.get("OPENROUTER_BASE_URL") or "https://openrouter.ai/api/v1").rstrip("/"),
        "модель": os.environ.get("EMBEDDING_MODEL", "google/gemini-embedding-001"),
        "мерность": int(os.environ.get("EMBEDDING_DIM", "3072")),
    }


def мерность_эмбеддинга() -> int:
    """Мерность вектора из конфига — под неё заведён столбец `halfvec(N)` в БД."""
    return int(os.environ.get("EMBEDDING_DIM", "3072"))


def модель_эмбеддингов() -> str:
    return os.environ.get("EMBEDDING_MODEL", "google/gemini-embedding-001")


async def _запросить_батч(client: httpx.AsyncClient, тексты: list[str], н: dict) -> list[list[float]]:
    body = {"model": н["модель"], "input": тексты}
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {н['ключ']}"}

    last: Exception | None = None
    for попытка in range(1, MAX_ПОПЫТОК + 1):
        try:
            res = await client.post(f"{н['база']}/embeddings", json=body, headers=headers)
            text = res.text
            if res.status_code == 429 or res.status_code >= 500:
                raise EmbeddingError(f"OpenRouter embeddings {res.status_code} (временная): {text[:200]}", retry=True)
            if res.status_code >= 400:
                raise EmbeddingError(f"OpenRouter embeddings {res.status_code}: {text[:400]}", retry=False)
            data = res.json()
            if data.get("error"):
                raise EmbeddingError(f"OpenRouter embeddings: {data['error'].get('message', data['error'])}", retry=True)
            items = data.get("data") or []
            if len(items) != len(тексты):
                raise EmbeddingError(
                    f"OpenRouter embeddings вернул {len(items)} векторов на {len(тексты)} текстов", retry=True
                )
            # Порядок не гарантирован — раскладываем по index.
            векторы: list[list[float] | None] = [None] * len(тексты)
            for it in items:
                вект = it["embedding"]
                if len(вект) != н["мерность"]:
                    raise EmbeddingError(
                        f"Модель {н['модель']} вернула вектор мерности {len(вект)}, "
                        f"ожидалось {н['мерность']} — проверьте EMBEDDING_DIM и столбец halfvec(N).",
                        retry=False,
                    )
                векторы[it["index"]] = вект
            return векторы  # type: ignore[return-value]
        except EmbeddingError as e:
            last = e
            if not e.retry or попытка == MAX_ПОПЫТОК:
                raise
            await asyncio.sleep(0.8 * попытка)
        except (httpx.TransportError, httpx.TimeoutException) as e:
            last = e
            if попытка == MAX_ПОПЫТОК:
                raise
            await asyncio.sleep(0.8 * попытка)
    assert last is not None
    raise last


async def посчитать_эмбеддинги(тексты: list[str], on_progress=None) -> list[list[float]]:
    """Эмбеддинги списка текстов в том же порядке. Бьёт на батчи по 64."""
    if not тексты:
        return []
    н = _настройки()
    итог: list[list[float]] = []
    async with httpx.AsyncClient(timeout=120.0) as client:
        for начало in range(0, len(тексты), РАЗМЕР_БАТЧА):
            батч = тексты[начало : начало + РАЗМЕР_БАТЧА]
            итог.extend(await _запросить_батч(client, батч, н))
            if on_progress:
                on_progress(min(начало + РАЗМЕР_БАТЧА, len(тексты)), len(тексты))
    return итог


async def посчитать_эмбеддинг(текст: str) -> list[float]:
    """Эмбеддинг одного текста (запрос покупателя)."""
    вект = await посчитать_эмбеддинги([текст])
    return вект[0]


def в_литерал_вектора(вектор: list[float]) -> str:
    """Литерал pgvector/halfvec: `[0.1,0.2,...]`."""
    return "[" + ",".join(repr(x) for x in вектор) + "]"
