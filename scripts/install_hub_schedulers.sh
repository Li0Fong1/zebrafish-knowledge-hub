#!/usr/bin/env bash
# Install / document Hub maintenance jobs (weekly lit, 3-day gallery, hourly stats).
# Prefer crontab; if cron not allowed, leave runnable scripts + note below.
set -euo pipefail
BASE="${ZEBRA_BASE:-/data/zoujin/deng/zebrafish_gene_qa}"
RT="${ZEBRA_RUNTIME:-/data/zoujin/deng/zebrafish_runtime}"
PY="${ZEBRA_PY:-/data/zoujin/deng/miniconda3/envs/zebrafish/bin/python}"
LOGDIR="$RT/logs"
mkdir -p "$LOGDIR"

WEEKLY="$BASE/scripts/job_weekly_lit_refresh.py"
GALLERY="$BASE/scripts/job_gallery_seed.py"
STATS="$BASE/scripts/job_refresh_hub_stats.py"

cat >"$LOGDIR/_hub_scheduler_note.txt" <<EOF
Zebrafish Hub schedulers
========================
Scripts (always runnable manually):
  $PY $WEEKLY
  $PY $GALLERY
  ZF_HUB_URL=http://127.0.0.1:8080 $PY $STATS

Suggested crontab (user deng):
  # weekly literature refresh — Sunday 03:20
  20 3 * * 0  cd $BASE && ZF_LIT_PER_QUERY=40 ZF_LIT_INCLUDE_GENES=0 $PY $WEEKLY >>$LOGDIR/job_weekly_lit.log 2>&1
  # gallery seed every 3 days — 04:10
  10 4 */3 * *  cd $BASE && $PY $GALLERY >>$LOGDIR/job_gallery_seed.log 2>&1
  # stats warm hourly
  5 * * * *  ZF_HUB_URL=http://127.0.0.1:8080 $PY $STATS >>$LOGDIR/job_stats.log 2>&1

If crontab -e is forbidden, run the three scripts from a long-lived screen/tmux
or systemd user timers with the same commands.
EOF

echo "NOTE_WRITTEN $LOGDIR/_hub_scheduler_note.txt"

if command -v crontab >/dev/null 2>&1; then
  TMP=$(mktemp)
  crontab -l 2>/dev/null | grep -v 'job_weekly_lit_refresh\|job_gallery_seed\|job_refresh_hub_stats' >"$TMP" || true
  cat >>"$TMP" <<EOF
20 3 * * 0  cd $BASE && ZF_LIT_PER_QUERY=40 ZF_LIT_INCLUDE_GENES=0 $PY $WEEKLY >>$LOGDIR/job_weekly_lit.log 2>&1
10 4 */3 * *  cd $BASE && $PY $GALLERY >>$LOGDIR/job_gallery_seed.log 2>&1
5 * * * *  ZF_HUB_URL=http://127.0.0.1:8080 $PY $STATS >>$LOGDIR/job_stats.log 2>&1
EOF
  if crontab "$TMP" 2>/dev/null; then
    echo "CRON_INSTALLED"
    crontab -l | grep 'job_' || true
  else
    echo "CRON_DENIED — use scripts + $LOGDIR/_hub_scheduler_note.txt"
  fi
  rm -f "$TMP"
else
  echo "NO_CRONTAB — use scripts + note"
fi
exit 0
