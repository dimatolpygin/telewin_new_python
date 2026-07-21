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
LOCK="${TELEWIN_LOCK:-$ROOT/.update.lock.d}"

# docker | docker-compose — что доступно
if docker compose version >/dev/null 2>&1; then
  DC="docker compose"
else
  DC="docker-compose"
fi

log() { echo "$(date '+%F %T') $*" >> "$LOG"; }

# Лок: атомарный mkdir — второй параллельный поллинг сразу выходит (долгий re-embed не наслаивается).
if ! mkdir "$LOCK" 2>/dev/null; then
  log "уже выполняется (lock $LOCK) — выход"
  exit 0
fi
trap 'rmdir "$LOCK" 2>/dev/null || true' EXIT

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
  log "updater завершился с кодом $CODE (ошибка) — рестарт НЕ делаю, БД не тронута"
fi

# Наружу отдаём 0 (для cron): факт «ошибка updater» уже в логе, cron не должен слать письма на код !=0.
exit 0
