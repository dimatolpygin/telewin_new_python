# -*- coding: utf-8 -*-
"""Адаптер канала VK (Bots Long Poll) поверх ядра `Yadro` (этап 27).

Сообщество «Домашний мастер» (`dmberez`, id 214164686). Токен — community
(`VK_TOKEN`), режим — Bots Long Poll: сервер приёма берём через
`groups.getLongPollServer`, затем крутим `act=a_check`. Тонкий транспорт: событие
`message_new` → `yadro.obrabotat("vk", peer_id, …)` → `messages.send`.

Устойчивость: `failed:1` → сдвинуть `ts`; `failed:2/3` → перезапросить key(+ts);
сетевые ошибки longpoll — пауза и переподключение без падения процесса.
"""
import asyncio
import json
import random

import httpx

from ..config import Config
from ..core import Yadro
from ..logger import (logger, nachat_zapros, log_vhodyashchee,
                      log_ishodyashchee, log_oshibka)
from ..texts import WELCOME, RESET_OK, ERROR_RETRY

CHANNEL = "vk"
API = "https://api.vk.com/method"
V = "5.199"

_WELCOME_CMD = {"начать", "start", "/start"}
_RESET_CMD = {"сброс", "reset", "/reset"}


class _VkApi:
    """Тонкий клиент VK API поверх общего httpx-клиента."""

    def __init__(self, client: httpx.AsyncClient, token: str):
        self._client = client
        self._token = token

    async def call(self, method: str, **params) -> dict:
        params.update(access_token=self._token, v=V)
        r = await self._client.post(f"{API}/{method}", data=params)
        data = r.json()
        if "error" in data:
            raise RuntimeError(f"VK API {method}: {data['error']}")
        return data["response"]

    async def send(self, peer_id: int, text: str) -> None:
        # random_id != 0 — VK так дедуплицирует отправку
        await self.call("messages.send", peer_id=peer_id, message=text,
                        random_id=random.randint(1, 2_000_000_000))


async def _get_server(api: _VkApi, group_id: str) -> tuple[str, str, str]:
    resp = await api.call("groups.getLongPollServer", group_id=group_id)
    server = resp["server"]
    if not server.startswith("http"):  # VK иногда отдаёт server без схемы
        server = "https://" + server
    return server, resp["key"], resp["ts"]


def _payload_start(msg: dict) -> bool:
    """Кнопка «Начать» шлёт payload {"command":"start"} — распознаём как welcome."""
    raw = msg.get("payload")
    if not raw:
        return False
    try:
        return "start" in json.dumps(json.loads(raw), ensure_ascii=False).lower()
    except (json.JSONDecodeError, TypeError):
        return False


async def _handle(msg: dict, api: _VkApi, yadro: Yadro) -> None:
    """Обработать одно входящее сообщение VK (в своей задаче → свой request-id)."""
    peer_id = msg["peer_id"]
    from_id = msg.get("from_id")
    text = (msg.get("text") or "").strip()
    with nachat_zapros(CHANNEL):
        log_vhodyashchee(None, from_id, None, text or "(без текста)")
        try:
            low = text.lower()
            if low in _WELCOME_CMD or _payload_start(msg):
                await api.send(peer_id, WELCOME)
                log_ishodyashchee(str(from_id), "приветствие")
                return
            if low in _RESET_CMD:
                await yadro.sbros(CHANNEL, peer_id)
                await api.send(peer_id, RESET_OK)
                return
            if not text:
                return
            res = await yadro.obrabotat(CHANNEL, peer_id, text)
            await api.send(peer_id, res.answer)
            log_ishodyashchee(str(from_id), res.answer,
                              meta=f"[поиск: {res.zaprosy_poiska}, найдено: {res.naydeno}]")
        except Exception:
            log_oshibka("Ошибка обработки сообщения", zapros=text)
            try:
                await api.send(peer_id, ERROR_RETRY)
            except Exception:
                log_oshibka("Не удалось отправить ответ об ошибке")


async def run_vk(cfg: Config, yadro: Yadro) -> None:
    """Запустить VK-канал (Bots Long Poll). Нет токена/группы → канал не поднимается."""
    if not cfg.vk_token or not cfg.vk_group_id:
        logger.info(f"Канал {CHANNEL}: VK_TOKEN/VK_GROUP_ID не заданы — канал выключен")
        return

    async with httpx.AsyncClient(timeout=30.0) as client:
        api = _VkApi(client, cfg.vk_token)
        server, key, ts = await _get_server(api, cfg.vk_group_id)
        logger.info(f"Канал {CHANNEL}: Bots Long Poll подключён (группа {cfg.vk_group_id})")

        while True:
            try:
                r = await client.get(server, params={"act": "a_check", "key": key,
                                                     "ts": ts, "wait": 25})
                upd = r.json()
            except (httpx.TransportError, httpx.TimeoutException) as e:
                logger.warning(f"[{CHANNEL}] longpoll сеть: {e}; переподключаюсь через 1с")
                await asyncio.sleep(1)
                continue

            if "failed" in upd:
                f = upd["failed"]
                if f == 1:  # только устаревший ts — берём новый
                    ts = upd["ts"]
                else:       # 2 (key протух) / 3 (нужны key+ts) — перезапрос
                    server, key, ts = await _get_server(api, cfg.vk_group_id)
                continue

            ts = upd["ts"]
            for u in upd.get("updates", []):
                if u.get("type") != "message_new":
                    continue
                obj = u.get("object", {})
                msg = obj.get("message", obj)  # v5.100+ кладёт в object.message
                # каждое сообщение — своя задача (свой request-id); очередь на
                # (канал, peer) держит ядро, поэтому один собеседник не гоняется
                asyncio.create_task(_handle(msg, api, yadro))


async def _standalone() -> None:
    """Отдельный запуск VK-канала для UAT этапа 27: python -m bot.channels.vk
    (оркестрация трёх каналов в одном процессе — этап 29)."""
    from ..config import load_config
    from ..bootstrap import sozdat_yadro
    cfg = load_config()
    yadro, pool, sessions = await sozdat_yadro(cfg)
    try:
        await run_vk(cfg, yadro)
    finally:
        await sessions.zakryt()
        await pool.close()


if __name__ == "__main__":
    try:
        asyncio.run(_standalone())
    except (KeyboardInterrupt, SystemExit):
        pass
