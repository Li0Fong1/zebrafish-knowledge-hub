#!/usr/bin/env bash
# unsent 金标解析 + 后处理（repair）+ 向量化（GPU 旁路，不杀 Hub）
set -euo pipefail
RT=/data/zoujin/deng/zebrafish_runtime
BASE=/data/zoujin/deng/zebrafish_gene_qa
PY=/data/zoujin/deng/miniconda3/envs/zebrafish/bin/python
INBOX=$RT/gold_ingest/pdfku_inbox/pdf
GOLD=$RT/gold_ingest/yes_fulltext_gold
LOGDIR=$RT/logs
ENO_LIST=$RT/logs/unsent_entry_nos.txt
PARSE_PY=$RT/gold_ingest/scripts/gold_parse_on_server.py
export ZEBRA_RUNTIME=$RT ZEBRA_BASE=$BASE PROJECT=$BASE PYTHONPATH=$BASE
export GOLD_OUT=$GOLD
export SCIHUB_INBOX=$INBOX
export FAISS_QWEN3_4B_DIR=$RT/faiss_pubmed_yes_qwen3_4b_v1
export QWEN3_4B_DIR=$RT/models/Qwen3-Embedding-4B
export EMBEDDING_DEVICE=${EMBEDDING_DEVICE:-cuda:1}
export PUBMED_EMBEDDING_DEVICE=${PUBMED_EMBEDDING_DEVICE:-cuda:1}

mkdir -p "$LOGDIR"
echo "=== unsent pipeline $(date -Is) ===" | tee -a "$LOGDIR/unsent_pipeline.log"

if [ ! -f "$ENO_LIST" ]; then
  echo "NO_ENTRY_LIST $ENO_LIST" | tee -a "$LOGDIR/unsent_pipeline.log"
  exit 2
fi

# 仅解析列表中、且尚未有金标 JSON 的 PDF
args=()
while read -r eno; do
  [ -z "$eno" ] && continue
  pdf="$INBOX/${eno}.pdf"
  [ -f "$pdf" ] || continue
  if find "$GOLD"/batch_*/papers -name "${eno}.json" 2>/dev/null | grep -q .; then
    continue
  fi
  args+=(--pdf "$pdf")
done < "$ENO_LIST"

echo "parse_pdf_args=${#args[@]}" | tee -a "$LOGDIR/unsent_pipeline.log"
if [ "${#args[@]}" -gt 0 ]; then
  # 分批，避免超长命令行
  batch=40
  i=0
  while [ $i -lt ${#args[@]} ]; do
    chunk=("${args[@]:$i:$batch}")
    echo "PARSE batch offset=$i n=${#chunk[@]}" | tee -a "$LOGDIR/unsent_pipeline.log"
    $PY -u "$PARSE_PY" "${chunk[@]}" --backfill \
      >>"$LOGDIR/unsent_gold_parse.log" 2>&1 || true
    i=$((i + batch))
  done
fi

echo "POST pipeline: vectorize first, then unsent-only repair" | tee -a "$LOGDIR/unsent_pipeline.log"
# 先向量化已有金标（避免全库 short repair 堵数小时），再仅修本批非金标
nohup $PY -u -m scripts.scihub_post_gold_pipeline --mode vectorize --gpu 1 \
  >>"$LOGDIR/unsent_vectorize.log" 2>&1 &
echo VEC_PID=$! | tee -a "$LOGDIR/unsent_pipeline.log"
# 后台：等向量化结束后，对本批 short 做有限 repair（不扫全库 scihub parse log）
nohup bash -c "
  while pgrep -f 'scihub_post_gold_pipeline --mode vectorize' >/dev/null; do sleep 30; done
  mapfile -t ENOS < <(awk 'NF{print}' \"$ENO_LIST\")
  args=()
  for eno in \"\${ENOS[@]}\"; do
    json=\$(find \"$GOLD\"/batch_*/papers -name \"\${eno}.json\" 2>/dev/null | head -1)
    if [ -z \"\$json\" ]; then args+=(--eno \"\$eno\"); continue; fi
    if ! grep -q '\"gold_tier\": \"pdf_gold\"\\|\"is_pdf_gold\": true' \"\$json\" 2>/dev/null; then
      args+=(--eno \"\$eno\")
    fi
  done
  echo \"unsent repair enos=\$(( \${#args[@]} / 2 ))\" >>\"$LOGDIR/unsent_pipeline.log\"
  if [ \${#args[@]} -gt 0 ]; then
    $PY -u -m scripts.scihub_post_gold_pipeline --mode repair --rounds 2 \"\${args[@]}\" \
      >>\"$LOGDIR/unsent_repair.log\" 2>&1 || true
    $PY -u -m scripts.scihub_post_gold_pipeline --mode vectorize --gpu 1 \
      >>\"$LOGDIR/unsent_vectorize.log\" 2>&1 || true
  fi
" >>"$LOGDIR/unsent_post_gold.log" 2>&1 &
echo POST_PID=$! | tee -a "$LOGDIR/unsent_pipeline.log"
echo "=== kicked $(date -Is) ===" | tee -a "$LOGDIR/unsent_pipeline.log"
