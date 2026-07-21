# Runbook — ежедневное автообновление прайса на Ubuntu-сервере (этап 24)

Как поставить воркер обновления прайса (`fetch с FTP → validate → parse → upsert → embed →
monitor → дата`) на расписание. **Сам стенд (бот + Postgres/pgvector + Redis) на сервер пока не
переезжает** — это отдельная веха; здесь только воркер обновления и таймер. Всё переносимо: код
не содержит Windows-специфики, пути и креды берутся из окружения.

## Что должно быть на сервере

- Python 3.11+ и venv проекта `tgbot_py/.venv` (`python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`);
- доступный Postgres с расширением `pgvector` и залитым каталогом (первичная заливка —
  `bot/import_price.py` + `bot/embed_index.py --all`, делается один раз);
- `.env` рядом с пакетом (см. `.env.example`), заполнены:
  - `PRICE_FTP_HOST/PORT/USER/PASSWORD/DIR` — приёмный FTP Форы;
  - `OPENROUTER_API_KEY` — для инкрементального re-embed новинок;
  - `PG*` — подключение к БД каталога.

## Быстрая проверка вручную

```bash
cd /opt/telewin/tgbot_py
./scripts/update.sh            # весь цикл; лог в logs/update.log, код возврата пробрасывается
```

- первый прогон дня применит свежий файл; повторный в тот же день → `цикл пропущен (skip-known)`;
- параллельный запуск (пока идёт первый) → `уже выполняется (lock …) — выход`, второй не наслаивается;
- сбой (FTP недоступен / пустышка / битый файл) → ненулевой код + строка `ERROR` в логе, БД не тронута.

## Установка через systemd (рекомендуется)

1. Подставь свои пути/пользователя в `deploy/telewin-update.service`
   (`WorkingDirectory`, `ExecStart`, `User`).
2. Скопируй юниты и включи таймер:

```bash
sudo cp deploy/telewin-update.service /etc/systemd/system/
sudo cp deploy/telewin-update.timer   /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now telewin-update.timer
```

3. Проверка:

```bash
systemctl list-timers telewin-update.timer     # когда следующий запуск
sudo systemctl start telewin-update.service    # прогнать прямо сейчас
journalctl -u telewin-update.service -n 50      # логи прогона
```

Окно запуска (`OnCalendar=*-*-* 06:00:00`) поправь под время, когда Фора уже залила файл
(время сервера UTC; клиент выгружает в своём поясе — сверься с `docs/АВТОЗАПУСК_диагностика.md`
соседнего проекта).

## Установка через cron (альтернатива)

```cron
0 6 * * * cd /opt/telewin/tgbot_py && ./scripts/update.sh >> logs/cron.log 2>&1
```

## Логи и эксплуатация

- лог прогона — `logs/update.log` (+ journald при systemd);
- новизна каталога (что просит словарной правки) — `docs/NOVELTY.md`, сводка последнего
  прогона — `docs/UPDATE_RUN.md`;
- временно выключить: `sudo systemctl disable --now telewin-update.timer`;
- «застрявший» лок после аварийного падения: удалить каталог `.update.lock.d` в корне проекта.

## Точка алерта

Ненулевой код возврата `update.sh` + строка `ERROR` в логе — точка подключения оповещения
(healthcheck, telegram-alert, `OnFailure=` у systemd-юнита). Само оповещение — вне скоупа этапа 24.
