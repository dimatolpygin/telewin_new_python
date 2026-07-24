# -*- coding: utf-8 -*-
"""Конфигурация из .env. Порт config.ts.
Читаем через python-dotenv; обязательные переменные — через must()."""
import os
from dataclasses import dataclass
from dotenv import load_dotenv

# .env лежит рядом с пакетом (корень tgbot_py)
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_ROOT, ".env"))


def must(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise SystemExit(f"Не задана переменная окружения {name} (см. .env.example)")
    return v


@dataclass(frozen=True)
class PgConfig:
    host: str
    port: int
    user: str
    password: str
    database: str
    schema: str


@dataclass(frozen=True)
class OpenRouterConfig:
    api_key: str
    model: str
    base_url: str


@dataclass(frozen=True)
class FtpConfig:
    host: str
    port: int
    user: str
    password: str
    directory: str


@dataclass(frozen=True)
class Config:
    bot_token: str
    openrouter: OpenRouterConfig
    pg: PgConfig
    redis_url: str
    price_xls: str
    ftp: FtpConfig | None
    # Каналы VK/MAX (этапы 27/28). Пусто → канал не поднимается.
    vk_token: str
    vk_group_id: str
    max_token: str
    # Телефон магазина для кнопки связи (этап 35). Пусто → кнопка отвечает,
    # что телефон недоступен; в коде номер не хардкодится.
    shop_phone: str
    # Кодовое слово выгрузки Excel-отчёта по диалогам (этап 38). Владелец пишет
    # его боту в Telegram → получает .xlsx. Секрет-гейт; меняется одной строкой.
    stats_code: str
    # Реальные адреса точек и часы работы (этап 36-Б). Пусто → бот НЕ выдумывает,
    # говорит «подскажут в магазине» (поведение 36-А). В коде улицы не хардкодим:
    # точка Микрорайон = столбец остатка `ostatok_mikro`, Берёзовская = `ostatok_berez`.
    shop_addr_mikro: str
    shop_addr_berez: str
    shop_hours: str


def load_config() -> Config:
    """Собирает конфиг, требуя критичные переменные. Вызывать при старте."""
    return Config(
        bot_token=must("BOT_TOKEN"),
        openrouter=OpenRouterConfig(
            api_key=must("OPENROUTER_API_KEY"),
            model=os.environ.get("OPENROUTER_MODEL", "anthropic/claude-haiku-4.5"),
            base_url="https://openrouter.ai/api/v1",
        ),
        pg=PgConfig(
            host=os.environ.get("PGHOST", "127.0.0.1"),
            port=int(os.environ.get("PGPORT", "5432")),
            user=os.environ.get("PGUSER", "postgres"),
            password=os.environ.get("PGPASSWORD", "postgres"),
            database=os.environ.get("PGDATABASE", "mydb"),
            schema=os.environ.get("PGSCHEMA", "telewin_test"),
        ),
        redis_url=os.environ.get("REDIS_URL", "redis://127.0.0.1:6379"),
        price_xls=os.environ.get("PRICE_XLS", ""),
        ftp=_ftp_config(),
        vk_token=os.environ.get("VK_TOKEN", "").strip(),
        vk_group_id=os.environ.get("VK_GROUP_ID", "").strip(),
        max_token=os.environ.get("MAX_TOKEN", "").strip(),
        shop_phone=os.environ.get("SHOP_PHONE", "").strip(),
        stats_code=os.environ.get("STATS_CODE", "/otchet63627").strip(),
        shop_addr_mikro=os.environ.get("SHOP_ADDR_MIKRO", "").strip(),
        shop_addr_berez=os.environ.get("SHOP_ADDR_BEREZ", "").strip(),
        shop_hours=os.environ.get("SHOP_HOURS", "").strip(),
    )


def _ftp_config() -> FtpConfig | None:
    """FTP-источник свежего прайса (этап 17). Нет хоста → None (работает локальный PRICE_XLS)."""
    host = os.environ.get("PRICE_FTP_HOST", "").strip()
    if not host:
        return None
    return FtpConfig(
        host=host,
        port=int(os.environ.get("PRICE_FTP_PORT", "21")),
        user=os.environ.get("PRICE_FTP_USER", ""),
        password=os.environ.get("PRICE_FTP_PASSWORD", ""),
        directory=os.environ.get("PRICE_FTP_DIR", "price"),
    )
