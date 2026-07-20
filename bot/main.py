# -*- coding: utf-8 -*-
"""Точка входа Python-бота. Этап 0 — заглушка: проверяет конфиг и логгер.
Реальный Telegram-слой (aiogram) добавляется на этапе 4.
"""
import asyncio

from bot.config import load_config
from bot.logger import logger


async def main() -> None:
    cfg = load_config()  # бросит SystemExit с понятной ошибкой, если нет BOT_TOKEN
    logger.info("Конфиг прочитан.")
    logger.info(f"  модель OpenRouter: {cfg.openrouter.model}")
    logger.info(f"  Postgres: {cfg.pg.host}:{cfg.pg.port}/{cfg.pg.database} (схема {cfg.pg.schema})")
    logger.info(f"  Redis: {cfg.redis_url}")
    logger.info("Каркас поднят. Telegram-слой подключается на этапе 4 (см. docs/ROADMAP.md).")


if __name__ == "__main__":
    asyncio.run(main())
