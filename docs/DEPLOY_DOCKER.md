# Docker-стек: app (TG+VK+MAX) + postgres-pgvector + redis (этап 30)

Локальный и прод-стек одним `docker compose`. Секреты — только в `.env` (env_file),
в образ не попадают. Хост БД/Redis внутри compose переопределяются на имена сервисов
(`db`, `redis`), поэтому localhost-значения из `.env` не мешают.

## Состав

- **`db`** — `pgvector/pgvector:pg16`. На чистом volume `docker/initdb/01-extension.sql`
  один раз заводит `CREATE EXTENSION vector` + схему `telewin_test`. Данные — том `pgdata`.
- **`redis`** — история диалога (том `redisdata`).
- **`app`** — `python -m bot.main`, три канала на общем ядре (индекс в памяти один раз).
- **`updater`** (profile `tools`, не поднимается с `up`) — одноразовый цикл обновления
  прайса из FTP; запуск по расписанию host-таймером (см. ниже).

## Первичная инициализация (чистый volume)

Порядок воспроизводим; последний шаг (эмбеддинги) — платный (~35 мин, OpenRouter).

```bash
# 0) .env заполнен (BOT_TOKEN, VK_TOKEN, VK_GROUP_ID, MAX_TOKEN, OPENROUTER_API_KEY, PG*, FTP*)
# 1) поднять БД и redis
docker compose up -d db redis            # db создаёт extension+схему из initdb

# 2) данные прайса: сгенерировать products.json из .xls (на сервере — из FTP-файла)
#    локально products.json уже лежит в ./data (том ./data монтируется в app)
#    при необходимости: python data/export_price.py <путь к .xls>

# 3) импорт прайса в БД (создаёт таблицу products + столбец embedding halfvec(3072))
docker compose run --rm app python -m bot.import_price

# 4) (платно, ~35 мин) эмбеддинги + HNSW-индекс — включает векторный канал поиска.
#    Без этого шага бот работает на чистой лексике (тоже отвечает).
docker compose run --rm app python -m bot.embed_index --all

# 5) запустить бота (три канала)
docker compose up -d app
docker compose logs -f app               # «Поиск готов: N товаров», подключение каналов
```

Проверка БД:
```bash
docker compose exec db psql -U postgres -d mydb -c \
  "select count(*) from telewin_test.products;"           # 11864
docker compose exec db psql -U postgres -d mydb -c "\d telewin_test.products" # embedding halfvec(3072)
```

## Обычный запуск

```bash
docker compose up -d          # db + redis + app (данные в pgdata сохраняются между рестартами)
docker compose logs -f app
docker compose down           # остановить (тома pgdata/redisdata остаются)
docker compose down -v        # ВНИМАНИЕ: снести и данные (потребует повторного init)
```

Выключить канал — оставить его токен в `.env` пустым (адаптер не поднимется).
Уровень логов — `LOG_LEVEL=info|debug` в `.env`.

## Автообновление прайса по расписанию (этап 24 в контейнере)

Один цикл (fetch с FTP → validate → upsert → монитор, эмбеддинги досчитываются новым):
```bash
docker compose run --rm updater
```
**Этап 33 — поллинг + условный рестарт бота.** Живой `app` держит индекс поиска в памяти
(грузится один раз на старте, `bot/bootstrap.py`), поэтому после обновления БД его надо перечитать.
Голый `docker compose run --rm updater` этого НЕ делает → бот не увидит новый прайс до деплоя.
Правильный запуск — через host-обёртку `scripts/update_docker.sh`: она гоняет `updater` и, ТОЛЬКО
если применён новый прайс с реальными изменениями (updater вернул код 10), делает `docker compose
restart app`. Время заливки прайса клиентом не фиксировано → ставим ПОЛЛИНГОМ (напр. каждые 30 мин):
```
*/30 * * * *  cd /opt/telewin/tgbot_py && ./scripts/update_docker.sh
```
Вхолостую дёшево: `--skip-known` сверяет имя свежего файла на FTP без скачивания; нет нового файла →
код 0 → рестарта (и простоя пуллеров TG/VK/MAX) нет. Рестарт случается только в момент появления
нового прайса, когда бы клиент его ни залил (сервер в UTC, магазины в Красноярске UTC+7 — при
поллинге неважно). Лог — `logs/update.cron.log`.

(systemd-юниты `deploy/telewin-update.{service,timer}` из этапа 24 — под host-native `update.sh`
без Docker; для Docker-прода используем cron + `update_docker.sh`.)

## Заметки

- **TLS MAX:** `certs/russian_trusted_{root,sub}_ca.cer` (Минцифры) лежат в образе — нужны
  для `platform-api2.max.ru`. Не удалять.
- Два инстанса на одном `BOT_TOKEN` (например, локальный `python -m bot.main` и контейнер)
  дают `TelegramConflictError` — Telegram отдаёт getUpdates только одному. Держать один.
- `pgdata`/`redisdata` — именованные тома, переживают `down` (без `-v`).
