#!/usr/bin/env bash
# Start full near-floor repair (NOT sample) + closeout if not running.
set +e
RT=/data/zoujin/deng/zebrafish_runtime
BASE=/data/zoujin/deng/zebrafish_gene_qa
PY=/data/zoujin/deng/miniconda3/envs/zebrafish/bin/python
LOG=$RT/logs
SCH=$LOG/scheduler
cd "$BASE"
export ZEBRA_RUNTIME=$RT ZEBRA_BASE=$BASE PROJECT=$BASE PYTHONPATH=$BASE
export SCIHUB_PARSE_LOG=$LOG/scihub_gold_nohup.log
export GOLD_OUT=$RT/gold_ingest/yes_fulltext_gold
export SCIHUB_INBOX=$RT/gold_ingest/pdfku_inbox/scihub_batch
export FAISS_QWEN3_4B_DIR=$RT/faiss_pubmed_yes_qwen3_4b_v1
export QWEN3_4B_DIR=$RT/models/Qwen3-Embedding-4B

if ! ps -u deng -o cmd | grep -F 'scripts.three_day_closeout' | grep -v grep >/dev/null; then
  nohup env CLOSEOUT_NEAR_MIN=15000 CLOSEOUT_REPAIR_BATCH=12 CLOSEOUT_REPAIR_ROUNDS=3 \
    CLOSEOUT_REPAIR_MAX_SEC=72000 CLOSEOUT_MAX_LOAD_RATIO=0.98 CLOSEOUT_MIN_AVAIL_GIB=10 \
    CLOSEOUT_GPU0_MIN_FREE_MB=200 CLOSEOUT_SLEEP_BUSY=5 \
    $PY -u -m scripts.three_day_closeout >$SCH/three_day_closeout_task.log 2>&1 &
  echo CLOSEOUT_KICKED=$!
else
  echo CLOSEOUT_ALREADY
fi

if ! ps -u deng -o cmd | grep -F 'scihub_post_gold_pipeline --mode repair' | grep -v grep >/dev/null; then
  nohup env SCIHUB_REPAIR_ROUNDS=8 SCIHUB_REPAIR_TIMEOUT=150 \
    $PY -u -m scripts.scihub_post_gold_pipeline --mode repair --rounds 8 \
    >$LOG/scihub_gold_repair_task.log 2>&1 &
  echo REPAIR_KICKED=$!
else
  echo REPAIR_ALREADY
fi

sleep 5
tail -n 10 $SCH/three_day_closeout_task.log
ps -u deng -o pid,etime,pcpu,cmd | grep -E 'three_day_closeout|scihub_post_gold' | grep -v grep | head -n 15
