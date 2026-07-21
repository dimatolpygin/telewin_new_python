# -*- coding: utf-8 -*-
"""История диалога в Redis по (канал, chat_id). TTL 30 мин, последние N сообщений.
Порт session.ts на redis.asyncio.

Ключ включает КАНАЛ (этап 25): один и тот же числовой id в разных каналах
(VK peer_id и TG chat_id) не должен схлопываться в одну историю.
"""
import json

import redis.asyncio as aioredis

from .config import Config

_TTL = 30 * 60  # сек
_MAX = 12       # последние сообщения (без системного)


def _key(channel: str, chat_id) -> str:
    return f"telewin:dialog:{channel}:{chat_id}"


class Sessions:
    def __init__(self, cfg: Config):
        self._r = aioredis.from_url(cfg.redis_url, decode_responses=True)

    async def zagruzit(self, channel: str, chat_id) -> list[dict]:
        raw = await self._r.get(_key(channel, chat_id))
        return json.loads(raw) if raw else []

    async def sohranit(self, channel: str, chat_id, msgs: list[dict]) -> None:
        trimmed = msgs[-_MAX:]
        await self._r.set(_key(channel, chat_id), json.dumps(trimmed, ensure_ascii=False), ex=_TTL)

    async def sbros(self, channel: str, chat_id) -> None:
        await self._r.delete(_key(channel, chat_id))

    async def zakryt(self) -> None:
        await self._r.aclose()
