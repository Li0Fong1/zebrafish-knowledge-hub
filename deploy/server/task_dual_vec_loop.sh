#!/usr/bin/env bash
# Dual GPU busy for real Qwen3 embed; yield both cards if pending gold appears.
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

ensure_gpu0_worker() {
  if ps -u deng -o cmd | grep -F 'scripts.gpu0_embed_worker' | grep -v grep >/dev/null; then
    return 0
  fi
  nohup env CUDA_VISIBLE_DEVICES=0 TOKENIZERS_PARALLELISM=false \
    FAISS_DISABLE_AVX2=1 PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True \
    ZEBRA_RUNTIME=$RT QWEN3_4B_DIR=$QWEN3_4B_DIR \
    GPU0_WORKER_BATCH=32 GPU0_WORKER_MICRO=8 GPU0_WORKER_SLEEP=0.02 \
    $PY -u -m scripts.gpu0_embed_worker >$SCH/gpu0_worker.log 2>&1 &
  echo GPU0_WORKER=$!
}

ensure_gpu1_pulse() {
  if ps -u deng -o cmd | grep -F 'scripts.hub_gpu1_pulse' | grep -v grep >/dev/null; then
    return 0
  fi
  nohup env ZEBRA_RUNTIME=$RT ZEBRA_BASE=$BASE \
    HUB_PULSE_SLEEP=0 HUB_PULSE_CONCURRENCY=4 HUB_PULSE_BATCH=24 HUB_PULSE_ROUNDS=6 \
    $PY -u -m scripts.hub_gpu1_pulse >$SCH/hub_gpu1_pulse.log 2>&1 &
  echo GPU1_PULSE=$!
}

while true; do
  echo "=== dual vec/gpu tick $(date -Is) ===" | tee -a $LOG/task_dual_vec.log
  $PY -u -m scripts.scihub_dual_gpu_vectorize --build-only >>$LOG/task_dual_vec.log 2>&1
  n=$($PY -c "from pathlib import Path;p=Path('$LOG/scihub_gold_vectorize_corpus.jsonl');print(sum(1 for _ in p.open()) if p.is_file() else 0)")
  echo pending_corpus=$n | tee -a $LOG/task_dual_vec.log

  if [ "${n:-0}" -gt 0 ]; then
    ps -u deng -o pid,cmd | grep -E 'gpu0_embed_worker|hub_gpu1_pulse|scihub_post_gold_pipeline --mode vectorize|scihub_dual_gpu_vectorize --partition' | grep -v grep | awk '{print $1}' | while read -r p; do kill $p 2>/dev/null || true; done
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
      sleep 12
    done
    echo partitions_done $(date -Is) | tee -a $LOG/task_dual_vec.log
  else
    ensure_gpu0_worker
    ensure_gpu1_pulse
    nvidia-smi --query-gpu=index,utilization.gpu,memory.used --format=csv,noheader | tee -a $LOG/task_dual_vec.log
    sleep 20
  fi
done
