# -*- coding: utf-8 -*-
"""Адаптер канала MAX (Long Polling) поверх ядра `Yadro` (этап 28).

Бот `id420320118936_bot` («Домашний мастер»). API MAX (dev.max.ru):
- база **`https://platform-api2.max.ru`**;
- токен — **заголовком** `Authorization: <токен>` (query-параметр `?access_token=`
  больше не поддерживается);
- Long Polling: `GET /updates?marker=&timeout=&limit=` → `{updates, marker}`;
  `marker` из ответа передаём в следующий запрос (курсор прочитанного);
- отправка: `POST /messages?chat_id=<id>` с телом `{"text": …}`;
- **TLS:** сервер под сертификатом Минцифры — стандартного certifi мало, поэтому
  строим bundle certifi ⊕ корневой/промежуточный Минцифры (`certs/`).

Тонкий транспорт: `message_created` → `yadro.obrabotat("max", chat_key, …)`.
"""
import asyncio
import os
import ssl

import certifi
import httpx

from ..config import Config
from ..core import Yadro
from ..keyboards import MAX_KOMANDY, max_nuzhna_svyaz
from ..logger import (logger, nachat_zapros, log_vhodyashchee,
                      log_ishodyashchee, log_oshibka)
from ..texts import WELCOME, RESET_OK, ERROR_RETRY, kontakt

CHANNEL = "max"
BASE = "https://platform-api2.max.ru"
# tgbot_py/ (корень репо) → certs/
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_CERTS = os.path.join(_ROOT, "certs")
_MINCIFRY = ("russian_trusted_root_ca.cer", "russian_trusted_sub_ca.cer")

_WELCOME_CMD = {"начать", "start", "/start"}
_RESET_CMD = {"сброс", "reset", "/reset"}


def _ssl_context() -> ssl.SSLContext:
    """certifi ⊕ сертификаты Минцифры — иначе TLS к platform-api2.max.ru падает."""
    ctx = ssl.create_default_context(cafile=certifi.where())
    for name in _MINCIFRY:
        p = os.path.join(_CERTS, name)
        if os.path.exists(p):
            ctx.load_verify_locations(cafile=p)
        else:
            logger.warning(f"[{CHANNEL}] нет сертификата {name} в {_CERTS} — TLS может упасть")
    return ctx


class _MaxApi:
    def __init__(self, client: httpx.AsyncClient, token: str):
        self._client = client
        self._headers = {"Authorization": token}

    async def get_updates(self, marker: int | None, timeout: int) -> dict:
        params = {"timeout": timeout, "limit": 100,
                  "types": "message_created,bot_started"}
        if marker is not None:
            params["marker"] = marker
        r = await self._client.get(f"{BASE}/updates", headers=self._headers, params=params)
        r.raise_for_status()
        return r.json()

    async def send(self, chat_id: int, text: str) -> None:
        r = await self._client.post(
            f"{BASE}/messages", headers={**self._headers, "Content-Type": "application/json"},
            params={"chat_id": chat_id}, json={"text": text},
        )
        r.raise_for_status()

    async def ustanovit_komandy(self, komandy: list[dict]) -> None:
        """Меню команд бота (этап 35): `PATCH /me`, поле `commands`. Держим в коде,
        а не разово руками, чтобы меню пережило пересоздание бота и было видно в репо."""
        r = await self._client.patch(
            f"{BASE}/me", headers={**self._headers, "Content-Type": "application/json"},
            json={"commands": komandy},
        )
        r.raise_for_status()


def _razobrat(u: dict) -> tuple[int | None, str, object, str]:
    """Из Update вытащить (chat_id, text, from_id, тип). Поддержаны message_created
    и bot_started (запуск бота по диплинку/кнопке «начать»)."""
    typ = u.get("update_type")
    if typ == "message_created":
        msg = u.get("message", {})
        rec = msg.get("recipient", {})
        chat_id = rec.get("chat_id") or rec.get("user_id")
        text = (msg.get("body", {}).get("text") or "").strip()
        from_id = (msg.get("sender") or {}).get("user_id")
        return chat_id, text, from_id, typ
    if typ == "bot_started":
        return u.get("chat_id"), "", (u.get("user") or {}).get("user_id"), typ
    return None, "", None, typ or "?"


async def _handle(u: dict, api: _MaxApi, yadro: Yadro, phone: str) -> None:
    """Обработать одно обновление MAX (в своей задаче → свой request-id)."""
    chat_id, text, from_id, typ = _razobrat(u)
    if chat_id is None:
        return
    with nachat_zapros(CHANNEL):
        log_vhodyashchee(None, from_id, None, text or f"({typ})")
        try:
            if typ == "bot_started":
                await api.send(chat_id, WELCOME)
                log_ishodyashchee(str(from_id), "приветствие")
                return
            low = text.lower()
            # команда связи из меню бота (этап 35): отвечаем телефоном, поиск не трогаем
            if max_nuzhna_svyaz(text):
                await api.send(chat_id, kontakt(phone))
                log_ishodyashchee(str(from_id), "телефон магазина (команда связи)")
                return
            if low in _WELCOME_CMD:
                await api.send(chat_id, WELCOME)
                log_ishodyashchee(str(from_id), "приветствие")
                return
            if low in _RESET_CMD:
                await yadro.sbros(CHANNEL, chat_id)
                await api.send(chat_id, RESET_OK)
                return
            if not text:
                return
            res = await yadro.obrabotat(CHANNEL, chat_id, text)
            await api.send(chat_id, res.answer)
            log_ishodyashchee(str(from_id), res.answer,
                              meta=f"[поиск: {res.zaprosy_poiska}, найдено: {res.naydeno}]")
        except Exception:
            log_oshibka("Ошибка обработки сообщения", zapros=text)
            try:
                await api.send(chat_id, ERROR_RETRY)
            except Exception:
                log_oshibka("Не удалось отправить ответ об ошибке")


async def run_max(cfg: Config, yadro: Yadro) -> None:
    """Запустить MAX-канал (Long Polling). Нет токена → канал не поднимается."""
    if not cfg.max_token:
        logger.info(f"Канал {CHANNEL}: MAX_TOKEN не задан — канал выключен")
        return

    async with httpx.AsyncClient(timeout=40.0, verify=_ssl_context()) as client:
        api = _MaxApi(client, cfg.max_token)
        # меню команд бота — best-effort: не встало, значит нет меню, но канал живёт
        try:
            await api.ustanovit_komandy(MAX_KOMANDY)
        except Exception:
            logger.warning(f"[{CHANNEL}] не удалось выставить меню команд")
        # Слив стартового курсора: берём текущий marker БЕЗ обработки, чтобы после
        # рестарта не переотвечать на старые сообщения. Дальше — только новые.
        try:
            nach = await api.get_updates(marker=None, timeout=0)
            marker = nach.get("marker")
        except Exception:
            log_oshibka(f"[{CHANNEL}] не удалось получить стартовый marker")
            marker = None
        logger.info(f"Канал {CHANNEL}: Long Polling подключён (бот id420320118936_bot)")

        while True:
            try:
                resp = await api.get_updates(marker=marker, timeout=30)
            except (httpx.TransportError, httpx.TimeoutException) as e:
                logger.warning(f"[{CHANNEL}] longpoll сеть: {e}; переподключаюсь через 1с")
                await asyncio.sleep(1)
                continue
            except httpx.HTTPStatusError as e:
                logger.warning(f"[{CHANNEL}] longpoll HTTP {e.response.status_code}; пауза 3с")
                await asyncio.sleep(3)
                continue

            for u in resp.get("updates", []):
                # каждое сообщение — своя задача (свой request-id); очередь на
                # (канал, chat) держит ядро
                asyncio.create_task(_handle(u, api, yadro, cfg.shop_phone))
            marker = resp.get("marker", marker)


async def _standalone() -> None:
    """Отдельный запуск MAX-канала для UAT этапа 28: python -m bot.channels.max."""
    from ..config import load_config
    from ..bootstrap import sozdat_yadro
    cfg = load_config()
    yadro, pool, sessions = await sozdat_yadro(cfg)
    try:
        await run_max(cfg, yadro)
    finally:
        await sessions.zakryt()
        await pool.close()


if __name__ == "__main__":
    try:
        asyncio.run(_standalone())
    except (KeyboardInterrupt, SystemExit):
        pass
