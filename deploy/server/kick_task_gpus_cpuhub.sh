#!/usr/bin/env bash
# Free both GPUs for real work: Hub on CPU, repair + dual-vec loop.
set +e
RT=/data/zoujin/deng/zebrafish_runtime
BASE=/data/zoujin/deng/zebrafish_gene_qa
PY=/data/zoujin/deng/miniconda3/envs/zebrafish/bin/python
LOG=$RT/logs
SCH=$LOG/scheduler
cd "$BASE"

kill_match() {
  ps -u deng -o pid,cmd | grep -E "$1" | grep -v grep | awk '{print $1}' | while read -r p; do
    echo "kill $1 -> $p"; kill "$p" 2>/dev/null || true
  done
}

echo "=== kill demo + hub on GPU ==="
kill_match 'gpu0_embed_worker|hub_gpu1_pulse'
kill_match 'uvicorn api.api_server'
sleep 5
for i in $(seq 1 25); do
  u0=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits | sed -n '1p' | tr -d ' ')
  u1=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits | sed -n '2p' | tr -d ' ')
  echo drain $i gpu0=$u0 gpu1=$u1
  if [ "${u0:-0}" -lt 500 ] && [ "${u1:-0}" -lt 500 ]; then break; fi
  sleep 2
done

echo "=== hub CPU-only ==="
source /data/zoujin/deng/miniconda3/etc/profile.d/conda.sh
conda activate zebrafish
export PYTHONPATH=$BASE:$BASE/core:$BASE/database
set -a; source "$BASE/.env" 2>/dev/null || true; set +a
export CUDA_VISIBLE_DEVICES=
export EMBEDDING_DEVICE=cpu
export LLM_DEVICE=cpu
export EMBEDDING_DIM=2048
export SKIP_KNOWLEDGE_FAISS_REBUILD=1
export FAISS_DISABLE_AVX2=1
export GPU1_BURN=0
export ZEBRA_RUNTIME=$RT ZEBRA_BASE=$BASE PROJECT=$BASE

nohup $PY -m uvicorn api.api_server:app --host 127.0.0.1 --port 8080 >>"$RT/logs/hub.log" 2>&1 &
echo $! >"$RT/hub.pid"
echo HUB_PID=$(cat $RT/hub.pid)
for i in $(seq 1 36); do
  H=$(curl -sS -m 15 http://127.0.0.1:8080/api/health 2>/dev/null || true)
  echo "$H" | grep -q '"status":"ok"' && echo HUB_OK && break
  sleep 5
done

if ! ps -u deng -o cmd | grep -F 'scripts.three_day_closeout' | grep -v grep >/dev/null; then
  nohup env ZEBRA_RUNTIME=$RT ZEBRA_BASE=$BASE PROJECT=$BASE PYTHONPATH=$BASE \
    CLOSEOUT_NEAR_MIN=15000 CLOSEOUT_REPAIR_BATCH=12 CLOSEOUT_REPAIR_ROUNDS=3 \
    CLOSEOUT_REPAIR_MAX_SEC=72000 CLOSEOUT_MAX_LOAD_RATIO=0.98 CLOSEOUT_MIN_AVAIL_GIB=10 \
    CLOSEOUT_GPU0_MIN_FREE_MB=200 CLOSEOUT_SLEEP_BUSY=5 \
    SCIHUB_PARSE_LOG=$LOG/scihub_gold_nohup.log \
    GOLD_OUT=$RT/gold_ingest/yes_fulltext_gold \
    SCIHUB_INBOX=$RT/gold_ingest/pdfku_inbox/scihub_batch \
    FAISS_QWEN3_4B_DIR=$RT/faiss_pubmed_yes_qwen3_4b_v1 \
    QWEN3_4B_DIR=$RT/models/Qwen3-Embedding-4B \
    $PY -u -m scripts.three_day_closeout >$SCH/three_day_closeout_task.log 2>&1 &
  echo CLOSEOUT_REKICK=$!
fi

if ! ps -u deng -o cmd | grep -F 'scihub_post_gold_pipeline --mode repair' | grep -v grep >/dev/null; then
  nohup env ZEBRA_RUNTIME=$RT ZEBRA_BASE=$BASE PROJECT=$BASE PYTHONPATH=$BASE \
    SCIHUB_PARSE_LOG=$LOG/scihub_gold_nohup.log \
    GOLD_OUT=$RT/gold_ingest/yes_fulltext_gold \
    SCIHUB_INBOX=$RT/gold_ingest/pdfku_inbox/scihub_batch \
    SCIHUB_REPAIR_ROUNDS=8 SCIHUB_REPAIR_TIMEOUT=150 \
    $PY -u -m scripts.scihub_post_gold_pipeline --mode repair --rounds 8 \
    >$LOG/scihub_gold_repair_task.log 2>&1 &
  echo REPAIR_REKICK=$!
fi

kill_match 'task_dual_vec_loop'
sleep 1
nohup bash $RT/gold_ingest/scripts/task_dual_vec_loop.sh >$LOG/task_dual_vec_console.log 2>&1 &
echo DUAL=$!
echo $! >$LOG/task_dual_vec.pid

sleep 8
echo "=== STATUS ==="
nvidia-smi --query-gpu=index,utilization.gpu,memory.used,memory.total --format=csv,noheader
ps -u deng -o pid,etime,pcpu,cmd | grep -E 'three_day_closeout|scihub_post_gold|dual_gpu|task_dual|uvicorn|gpu0_embed|hub_gpu1' | grep -v grep | head -n 25
tail -n 8 $SCH/three_day_closeout_task.log
tail -n 5 $LOG/scihub_gold_repair_task.log
