#!/usr/bin/env bash
set -euo pipefail
RT=/data/zoujin/deng/zebrafish_runtime
BASE=/data/zoujin/deng/zebrafish_gene_qa
PY=/data/zoujin/deng/miniconda3/envs/zebrafish/bin/python
INBOX=$RT/gold_ingest/pdfku_inbox/scihub_batch
LOGDIR=$RT/logs
export ZEBRA_RUNTIME=$RT ZEBRA_BASE=$BASE PROJECT=$BASE PYTHONPATH=$BASE
export GOLD_OUT=$RT/gold_ingest/yes_fulltext_gold
export SCIHUB_INBOX=$INBOX
export SCIHUB_PARSE_LOG=$LOGDIR/scihub_gold_nohup.log
export FAISS_QWEN3_4B_DIR=$RT/faiss_pubmed_yes_qwen3_4b_v1
export QWEN3_4B_DIR=$RT/models/Qwen3-Embedding-4B

if ! ps -u deng -o cmd | grep -F 'scripts.three_day_closeout' | grep -v grep >/dev/null; then
  nohup env CLOSEOUT_NEAR_MIN=15000 $PY -u -m scripts.three_day_closeout \
    >$LOGDIR/scheduler/three_day_closeout_task.log 2>&1 &
  echo CLOSEOUT_KICKED=$!
fi

if ! ps -u deng -o cmd | grep -F 'scihub_post_gold_pipeline --mode repair' | grep -v grep >/dev/null; then
  nohup $PY -u -m scripts.scihub_post_gold_pipeline --mode repair --rounds 8 \
    >$LOGDIR/scihub_gold_repair_task.log 2>&1 &
  echo REPAIR_KICKED=$!
fi

bash $RT/gold_ingest/scripts/kick_task_gpus_cpuhub.sh 2>/dev/null || true

echo inbox_pdfs=$(find "$INBOX" -type f -iname '*.pdf' | wc -l)
nvidia-smi --query-gpu=index,utilization.gpu,memory.used --format=csv,noheader
