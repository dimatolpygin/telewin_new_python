# Образ приложения (3 канала на общем ядре, этап 30).
# python-slim достаточно: asyncpg/httpx/aiogram ставятся из wheel'ов, компилятор не нужен.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Зависимости — отдельным слоем (кешируются, пока requirements.txt не менялся).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Код (данные монтируются томом ./data — см. docker-compose.yml)
COPY . .

# non-root
RUN useradd --create-home app && chown -R app:app /app
USER app

# По умолчанию — бот (3 канала). Сервис updater переопределяет command на цикл обновления.
CMD ["python", "-m", "bot.main"]
