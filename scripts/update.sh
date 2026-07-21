#!/usr/bin/env bash
# Этап 24 — entrypoint ежедневного обновления прайса (Ubuntu cron/systemd).
#
# Делает: fetch с FTP → validate → parse → upsert → embed → monitor → дата, один прогон.
# Идемпотентно (diff по штрихкоду) + `--skip-known` (не гонять на неизменившемся прайсе).
# Переносимо: POSIX, лок через `mkdir` (атомарно на любой ОС), venv-детект Ubuntu/Windows.
#
# Запуск вручную:  ./scripts/update.sh
# Через systemd:   см. deploy/telewin-update.{service,timer} и docs/DEPLOY_UPDATE.md
#
# Переменные окружения (необязательные):
#   TELEWIN_LOG_DIR — куда писать лог (по умолчанию <проект>/logs)
#   TELEWIN_LOCK    — каталог-лок (по умолчанию <проект>/.update.lock.d)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

LOG_DIR="${TELEWIN_LOG_DIR:-$ROOT/logs}"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/update.log"
LOCK="${TELEWIN_LOCK:-$ROOT/.update.lock.d}"

log() { echo "$(date '+%F %T') $*" | tee -a "$LOG"; }

# venv: Ubuntu → .venv/bin/python, Windows → .venv/Scripts/python.exe, иначе системный python3
if [ -x "$ROOT/.venv/bin/python" ]; then
  PY="$ROOT/.venv/bin/python"
elif [ -x "$ROOT/.venv/Scripts/python.exe" ]; then
  PY="$ROOT/.venv/Scripts/python.exe"
else
  PY="python3"
fi

# Лок: атомарный mkdir — второй параллельный запуск сразу выходит, БД не наслаивается.
if ! mkdir "$LOCK" 2>/dev/null; then
  log "уже выполняется (lock $LOCK) — выход"
  exit 0
fi
trap 'rmdir "$LOCK" 2>/dev/null || true' EXIT

log "=== старт обновления прайса ==="
set +e
"$PY" -m bot.update.run --ftp --skip-known 2>&1 | tee -a "$LOG"
CODE=${PIPESTATUS[0]}
set -e
log "=== конец, код возврата $CODE ==="
exit "$CODE"
