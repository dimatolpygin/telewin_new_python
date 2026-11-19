#!/usr/bin/env bash
# Этап 33 — прод (Docker): поллер обновления прайса + УСЛОВНЫЙ рестарт бота.
#
# Живой `app` держит индекс поиска в памяти (грузится один раз на старте, bot/bootstrap.py),
# поэтому после обновления БД его надо перечитать. Этот скрипт гоняет одноразовый `updater`
# (fetch с FTP → validate → upsert → embed → monitor, `--skip-known`) и, ТОЛЬКО если применён
# новый прайс с реальными изменениями каталога (updater вернул код 10), рестартит `app`.
#
# Время заливки прайса клиентом не фиксировано (утро/конец дня), поэтому запускать ПОЛЛИНГОМ
# из host-cron, например каждые 30 минут:
#   */30 * * * * cd /opt/telewin/tgbot_py && ./scripts/update_docker.sh
# Дёшево вхолостую: `--skip-known` сверяет имя свежего файла на FTP БЕЗ скачивания; нет нового
# файла → updater выходит с кодом 0 → рестарта (и простоя пуллеров TG/VK/MAX) нет.
#
# Замечание по часовому поясу: сервер в UTC, магазины в Красноярске (UTC+7). При поллинге это
# неважно — рестарт случается в момент появления нового прайса, когда бы он ни пришёл.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

LOG_DIR="${TELEWIN_LOG_DIR:-$ROOT/logs}"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/update.cron.log"
LOCK="${TELEWIN_LOCK:-$ROOT/.update.lock}"

# docker | docker-compose — что доступно
if docker compose version >/dev/null 2>&1; then
  DC="docker compose"
else
  DC="docker-compose"
fi

log() { echo "$(date '+%F %T') $*" >> "$LOG"; }

# flock освобождает блокировку при закрытии дескриптора, даже при аварийном завершении.
# Сам файл остаётся на месте: удаление создало бы отдельный лок для следующего запуска.
exec 9>>"$LOCK" || { log "не удалось открыть файл блокировки $LOCK"; exit 1; }
flock -n -E 75 9
LOCK_CODE=$?
case "$LOCK_CODE" in
  0) ;;
  75)
    log "обновление уже выполняется (lock $LOCK) — выход"
    exit 0
    ;;
  *)
    log "не удалось взять блокировку $LOCK (код $LOCK_CODE)"
    exit "$LOCK_CODE"
    ;;
esac

log "=== поллинг прайса ==="
$DC run --rm updater >> "$LOG" 2>&1
CODE=$?

if [ "$CODE" -eq 10 ]; then
  log "применён новый прайс → рестарт app (перечит индекса поиска)"
  $DC restart app >> "$LOG" 2>&1
  log "app рестартнут (код $?)"
elif [ "$CODE" -eq 0 ]; then
  log "нового прайса нет (skip-known/без изменений) — рестарт не нужен"
else
  log "updater завершился с кодом $CODE (ошибка, см. лог выше) — рестарт НЕ делаю"
fi

# Наружу отдаём 0 (для cron): факт «ошибка updater» уже в логе, cron не должен слать письма на код !=0.
exit 0
