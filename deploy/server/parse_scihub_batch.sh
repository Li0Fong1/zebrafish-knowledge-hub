#!/usr/bin/env bash
set -eu
RUNTIME=/data/zoujin/deng/zebrafish_runtime
if [ -z "${INBOX:-}" ]; then
  export INBOX="$RUNTIME/gold_ingest/pdfku_inbox/scihub_batch"
fi
LOG=$RUNTIME/logs/scihub_gold_parse.out
echo "=== scihub gold parse start $(date -Is) inbox=$INBOX ===" | tee -a "$LOG"
bash "$RUNTIME/gold_ingest/scripts/gold_parse_on_server.sh" --inbox 2>&1 | tee -a "$LOG"
echo "=== scihub gold parse done $(date -Is) ===" | tee -a "$LOG"
