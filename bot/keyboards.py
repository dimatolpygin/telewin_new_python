# -*- coding: utf-8 -*-
"""Клавиатуры каналов (этап 35). Первая клавиатура в проекте — раньше их не было
ни в одном адаптере, поэтому схемы собраны здесь, а не размазаны по транспорту.

Кнопка одна: «Связаться с магазином» — по нажатию бот присылает телефон отдельным
сообщением (нативной кнопки «позвонить» у мессенджеров нет: ВК ждёт http-ссылку в
`open_link`, MAX — тоже url; номер в тексте сообщения на телефоне кликабелен и
открывает набор одним касанием, это рабочий путь).

Различие каналов, из-за которого схемы разные:
- **ВК** умеет НАСТОЯЩУЮ постоянную клавиатуру под полем ввода (`inline: false`,
  `one_time: false`). Нажатие приходит обычным `message_new` с текстом = label
  и нашим `payload`.
- **MAX** постоянной клавиатуры под полем ввода НЕ имеет — только inline-клавиатуру,
  прикреплённую к сообщению (`attachments[].type = "inline_keyboard"`). Поэтому
  кнопку вешаем на КАЖДЫЙ исходящий ответ бота: она всегда под последней репликой.
  Нажатие приходит апдейтом `message_callback` (нужен в подписке `types`).
"""
import json

# Текст на кнопке (ВК: до 40 символов).
KNOPKA_SVYAZ = "Связаться с магазином"

# Метка нажатия. ВК кладёт её в payload сообщения, MAX — в callback.payload.
CMD_SVYAZ = "shop_contact"

_VK_PAYLOAD = json.dumps({"cmd": CMD_SVYAZ}, ensure_ascii=False)


def vk_klaviatura() -> str:
    """Постоянная клавиатура ВК (JSON-строка для параметра `keyboard` в messages.send).

    `inline: false` — клавиатура под полем ввода, `one_time: false` — после нажатия
    не прячется (требование этапа: кнопка висит всегда).
    """
    return json.dumps({
        "one_time": False,
        "inline": False,
        "buttons": [[{
            "action": {"type": "text", "label": KNOPKA_SVYAZ, "payload": _VK_PAYLOAD},
            "color": "primary",
        }]],
    }, ensure_ascii=False)


def vk_nazhata_svyaz(text: str, payload_raw: str | None) -> bool:
    """Входящее сообщение ВК — это нажатие нашей кнопки связи?

    Сверяем и payload (надёжно), и сам текст: пользователь может напечатать подпись
    кнопки руками, ответ должен быть тот же.
    """
    if payload_raw:
        try:
            if json.loads(payload_raw).get("cmd") == CMD_SVYAZ:
                return True
        except (json.JSONDecodeError, TypeError, AttributeError):
            pass
    return text.strip().lower() == KNOPKA_SVYAZ.lower()


def tg_klaviatura():
    """Постоянная reply-клавиатура Telegram (этап 35, по решению заказчика — сверх
    исходного скоупа ВК+MAX). `is_persistent` держит её раскрытой, `resize_keyboard`
    ужимает под одну кнопку. Нажатие приходит обычным текстовым сообщением с подписью
    кнопки, поэтому распознаём его тем же `vk_nazhata_svyaz(text, None)` по тексту.

    aiogram импортируем внутри функции: модуль клавиатур общий для всех каналов, а
    ВК/MAX работают на raw httpx и тянуть в них aiogram незачем.
    """
    from aiogram.types import KeyboardButton, ReplyKeyboardMarkup
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=KNOPKA_SVYAZ)]],
        resize_keyboard=True, is_persistent=True,
    )


def max_klaviatura() -> list[dict]:
    """Inline-клавиатура MAX — список `attachments` для POST /messages."""
    return [{
        "type": "inline_keyboard",
        "payload": {"buttons": [[
            {"type": "callback", "text": KNOPKA_SVYAZ, "payload": CMD_SVYAZ},
        ]]},
    }]
