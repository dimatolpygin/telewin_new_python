# -*- coding: utf-8 -*-
"""Точка входа процесса: оркестрация каналов TG+VK+MAX на общем ядре (этап 29).

Индекс поиска (11 864 товара) грузится ОДИН раз (`sozdat_yadro`) и делится всеми
каналами. Каждый канал крутится своей задачей под супервизором: падение одного
логируется и не роняет остальные (перезапуск с паузой). Каналы без токена не
поднимаются (адаптеры возвращаются сразу) — можно работать подмножеством.
Корректное завершение по SIGTERM/SIGINT: отмена задач, закрытие redis/pool/сессий.
Запуск: python -m bot.main
"""
import asyncio
import signal

from .config import Config, load_config
from .logger import logger
from .bootstrap import sozdat_yadro
from .core import Yadro
from .channels.telegram import run_telegram
from .channels.vk import run_vk
from .channels.max import run_max

# (имя, функция-запуска) — единый список каналов. Добавить канал = одна строка.
KANALY = [("telegram", run_telegram), ("vk", run_vk), ("max", run_max)]

_RESTART_PAUZA = 5.0  # сек между падением канала и перезапуском


async def _supervise(name: str, fn, cfg: Config, yadro: Yadro) -> None:
    """Запустить канал под надзором. Штатное завершение (канал выключен) → выход.
    Падение → лог + перезапуск через паузу. Отмена (shutdown) пробрасывается."""
    while True:
        try:
            await fn(cfg, yadro)
            logger.info(f"Канал {name} завершил работу")
            return
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Канал {name} упал: {e}; перезапуск через {int(_RESTART_PAUZA)}с",
                         exc_info=True)
            await asyncio.sleep(_RESTART_PAUZA)


def _ustanovit_signaly(stop: asyncio.Event) -> None:
    """Best-effort обработчики SIGINT/SIGTERM → выставить событие остановки.
    На Windows add_signal_handler недоступен — там ловим KeyboardInterrupt в __main__."""
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, getattr(signal, "SIGTERM", None)):
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            try:
                signal.signal(sig, lambda *_: stop.set())
            except (ValueError, OSError):
                pass


async def main() -> None:
    cfg = load_config()
    yadro, pool, sessions = await sozdat_yadro(cfg)

    tasks = [asyncio.create_task(_supervise(name, fn, cfg, yadro), name=name)
             for name, fn in KANALY]

    stop = asyncio.Event()
    _ustanovit_signaly(stop)

    # Ждём: либо сигнал остановки, либо все каналы завершились (все выключены).
    stop_task = asyncio.create_task(stop.wait())
    vse_kanaly = asyncio.gather(*tasks, return_exceptions=True)
    try:
        await asyncio.wait({stop_task, vse_kanaly}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        logger.info("Останавливаюсь — закрываю каналы и соединения…")
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        stop_task.cancel()
        await sessions.zakryt()
        await pool.close()
        logger.info("Остановлен. Соединения закрыты.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
