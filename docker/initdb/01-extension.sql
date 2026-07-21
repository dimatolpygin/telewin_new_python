-- Инициализация чистого volume БД (этап 30). Выполняется ОДИН раз при первом
-- старте контейнера pgvector (docker-entrypoint-initdb.d), от суперпользователя,
-- в базе POSTGRES_DB. Таблицу products создаёт bot.import_price (он же дублирует
-- эти CREATE идемпотентно на случай существующего volume).
CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS telewin_test;
