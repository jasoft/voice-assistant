#!/bin/bash
# Cron-driven non-interactive Hermes update (--yes). hermes update
# performs the gateway restart itself as its final phase (designed for
# cron/non-interactive callers), so no explicit restart is done here.
LOG=/Users/weiwang/.hermes/logs/cron_update.log
{
  echo "=== cron update start $(date '+%F %T') ==="
  hermes --version
  echo "--- hermes update --yes ---"
  hermes update --yes </dev/null
  echo "update exit code: $?"
  echo "=== done $(date '+%F %T') ==="
} >> "$LOG" 2>&1
