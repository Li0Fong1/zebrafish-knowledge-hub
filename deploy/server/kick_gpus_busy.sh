#!/usr/bin/env bash
# GPU0: Qwen3 real embed (or dual-vec if pending gold)
# GPU1: Hub + pulse
# CPU: keep repair
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

# stop CPU-only hub so GPU1 can take the real embed model
kill_match 'uvicorn api.api_server'
kill_match 'hub_gpu1_pulse|gpu1_fill'
# do not kill repair / closeout / dual vec yet
sleep 4

source /data/zoujin/deng/miniconda3/etc/profile.d/conda.sh
conda activate zebrafish
export PYTHONPATH=$BASE:$BASE/core:$BASE/database
set -a; source "$BASE/.env" 2>/dev/null || true; set +a
export ZEBRA_RUNTIME=$RT ZEBRA_BASE=$BASE PROJECT=$BASE
export FAISS_DISABLE_AVX2=1
export SKIP_KNOWLEDGE_FAISS_REBUILD=1
export EMBEDDING_DIM=2048
export GPU1_BURN=0
export CUDA_VISIBLE_DEVICES=1
export EMBEDDING_DEVICE=cuda
export LLM_DEVICE=cpu

nohup $PY -m uvicorn api.api_server:app --host 127.0.0.1 --port 8080 >>"$RT/logs/hub.log" 2>&1 &
echo $! >"$RT/hub.pid"
echo HUB_PID=$(cat $RT/hub.pid)
for i in $(seq 1 40); do
  H=$(curl -sS -m 15 http://127.0.0.1:8080/api/health 2>/dev/null || true)
  echo "$H" | grep -q '"status":"ok"' && echo HUB_OK && break
  sleep 5
done

# GPU1 fill remaining compute via Hub embed warmup
nohup env ZEBRA_RUNTIME=$RT ZEBRA_BASE=$BASE HUB_PULSE_SLEEP=0 HUB_PULSE_CONCURRENCY=4 \
  HUB_PULSE_BATCH=24 HUB_PULSE_ROUNDS=6 HUB_PULSE_CHARS=1000 \
  $PY -u -m scripts.hub_gpu1_pulse >$SCH/hub_gpu1_pulse.log 2>&1 &
echo PULSE=$!

# rewrite dual-vec loop: pending gold -> both GPUs vectorize; else GPU0 embed worker
cat > $RT/gold_ingest/scripts/task_dual_vec_loop.sh <<'EOF'
#!/usr/bin/env bash
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
export QWEN3_4B_EMBED_BATCH=8

start_gpu0_embed() {
  if ps -u deng -o cmd | grep -F 'scripts.gpu0_embed_worker' | grep -v grep >/dev/null; then
    return 0
  fi
  nohup env CUDA_VISIBLE_DEVICES=0 TOKENIZERS_PARALLELISM=false \
    ZEBRA_RUNTIME=$RT QWEN3_4B_DIR=$QWEN3_4B_DIR \
    GPU0_WORKER_BATCH=32 GPU0_WORKER_MICRO=8 GPU0_WORKER_SLEEP=0 GPU0_WORKER_CHARS=1400 \
    $PY -u -m scripts.gpu0_embed_worker >$SCH/gpu0_embed_worker.log 2>&1 &
  echo GPU0_EMBED=$!
}

while true; do
  echo "=== dual vec tick $(date -Is) ===" | tee -a $LOG/task_dual_vec.log
  $PY -u -m scripts.scihub_dual_gpu_vectorize --build-only >>$LOG/task_dual_vec.log 2>&1
  n=$($PY -c "from pathlib import Path;p=Path('/data/zoujin/deng/zebrafish_runtime/logs/scihub_gold_vectorize_corpus.jsonl');print(sum(1 for _ in p.open()) if p.is_file() else 0)")
  echo pending_corpus=$n | tee -a $LOG/task_dual_vec.log

  if [ "${n:-0}" -gt 0 ]; then
    ps -u deng -o pid,cmd | grep -E 'gpu0_embed_worker|scihub_post_gold_pipeline --mode vectorize|scihub_dual_gpu_vectorize --partition' | grep -v grep | awk '{print $1}' | while read -r p; do kill $p 2>/dev/null || true; done
    sleep 3
    nohup env CUDA_VISIBLE_DEVICES=0 QWEN3_4B_EMBED_BATCH=8 \
      ZEBRA_RUNTIME=$RT ZEBRA_BASE=$BASE PYTHONPATH=$BASE \
      SCIHUB_PARSE_LOG=$SCIHUB_PARSE_LOG GOLD_OUT=$GOLD_OUT SCIHUB_INBOX=$SCIHUB_INBOX \
      FAISS_QWEN3_4B_DIR=$FAISS_QWEN3_4B_DIR QWEN3_4B_DIR=$QWEN3_4B_DIR \
      $PY -u -m scripts.scihub_dual_gpu_vectorize --partition 0/2 --gpu-label 0 \
      >>$LOG/task_vec_gpu0.log 2>&1 &
    echo VEC0=$!
    nohup env CUDA_VISIBLE_DEVICES=1 QWEN3_4B_EMBED_BATCH=8 \
      ZEBRA_RUNTIME=$RT ZEBRA_BASE=$BASE PYTHONPATH=$BASE \
      SCIHUB_PARSE_LOG=$SCIHUB_PARSE_LOG GOLD_OUT=$GOLD_OUT SCIHUB_INBOX=$SCIHUB_INBOX \
      FAISS_QWEN3_4B_DIR=$FAISS_QWEN3_4B_DIR QWEN3_4B_DIR=$QWEN3_4B_DIR \
      $PY -u -m scripts.scihub_dual_gpu_vectorize --partition 1/2 --gpu-label 1 \
      >>$LOG/task_vec_gpu1.log 2>&1 &
    echo VEC1=$!
    while ps -u deng -o cmd | grep 'scihub_dual_gpu_vectorize --partition' | grep -v grep >/dev/null; do
      nvidia-smi --query-gpu=index,utilization.gpu,memory.used --format=csv,noheader | tee -a $LOG/task_dual_vec.log
      sleep 15
    done
    echo partitions_done $(date -Is) | tee -a $LOG/task_dual_vec.log
  else
    start_gpu0_embed
    sleep 20
  fi
done
EOF
chmod +x $RT/gold_ingest/scripts/task_dual_vec_loop.sh
sed -i 's/\r$//' $RT/gold_ingest/scripts/task_dual_vec_loop.sh

kill_match 'task_dual_vec_loop'
sleep 1
nohup bash $RT/gold_ingest/scripts/task_dual_vec_loop.sh >$LOG/task_dual_vec_console.log 2>&1 &
echo DUAL=$!
echo $! >$LOG/task_dual_vec.pid

# GPU0 immediately (loop also starts it after first tick)
if ! ps -u deng -o cmd | grep -F 'scripts.gpu0_embed_worker' | grep -v grep >/dev/null; then
  nohup env CUDA_VISIBLE_DEVICES=0 TOKENIZERS_PARALLELISM=false \
    ZEBRA_RUNTIME=$RT QWEN3_4B_DIR=$RT/models/Qwen3-Embedding-4B \
    GPU0_WORKER_BATCH=32 GPU0_WORKER_MICRO=8 GPU0_WORKER_SLEEP=0 GPU0_WORKER_CHARS=1400 \
    $PY -u -m scripts.gpu0_embed_worker >$SCH/gpu0_embed_worker.log 2>&1 &
  echo GPU0_EMBED_NOW=$!
fi

sleep 18
echo '=== GPU ==='
nvidia-smi --query-gpu=index,utilization.gpu,memory.used,memory.total --format=csv,noheader
echo '=== PROCS ==='
ps -u deng -o pid,etime,pcpu,cmd | grep -E 'three_day_closeout|scihub_post_gold|dual_gpu|task_dual|uvicorn|gpu0_embed|hub_gpu1' | grep -v grep | head -n 20
echo '=== HUB ==='
curl -sS -m 10 http://127.0.0.1:8080/api/health 2>/dev/null | head -c 400; echo
