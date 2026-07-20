# -*- coding: utf-8 -*-
"""История диалога в Redis по chat_id. TTL 30 мин, последние N сообщений.
Порт session.ts на redis.asyncio.
"""
import json

import redis.asyncio as aioredis

from .config import Config

_TTL = 30 * 60  # сек
_MAX = 12       # последние сообщения (без системного)


def _key(chat_id: int) -> str:
    return f"telewin:dialog:{chat_id}"


class Sessions:
    def __init__(self, cfg: Config):
        self._r = aioredis.from_url(cfg.redis_url, decode_responses=True)

    async def zagruzit(self, chat_id: int) -> list[dict]:
        raw = await self._r.get(_key(chat_id))
        return json.loads(raw) if raw else []

    async def sohranit(self, chat_id: int, msgs: list[dict]) -> None:
        trimmed = msgs[-_MAX:]
        await self._r.set(_key(chat_id), json.dumps(trimmed, ensure_ascii=False), ex=_TTL)

    async def sbros(self, chat_id: int) -> None:
        await self._r.delete(_key(chat_id))

    async def zakryt(self) -> None:
        await self._r.aclose()
