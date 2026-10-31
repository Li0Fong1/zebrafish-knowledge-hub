#!/bin/bash
# GPU1: 先重建 v1 坏 title，再全库短语义块写入 v2。Hub 须已绑 GPU0。
set -euo pipefail
BASE=/data/zoujin/deng/zebrafish_gene_qa
RT=/data/zoujin/deng/zebrafish_runtime
PY=/data/zoujin/deng/miniconda3/envs/zebrafish/bin/python
export PROJECT=$BASE ZEBRA_BASE=$BASE ZEBRA_RUNTIME=$RT RUNTIME=$RT
export PYTHONPATH=$BASE:$BASE/core:$BASE/database
export CUDA_VISIBLE_DEVICES=1
export EMBEDDING_DEVICE=cuda:0
export PUBMED_YES_SECTION_CHUNKING=1
export EXIST_ENO_MAX_CHUNKS=0
export EXIST_ENO_MAX_BODY_CHARS=0
export QWEN3_4B_EMBED_BATCH=2
export QWEN3_4B_DOC_MICRO=32
export QWEN3_4B_SHARD_PAPERS=12
cd "$BASE"
echo "TITLE_REBUILD start $(date -Iseconds)" >>"$RT/logs/kick_short_reembed.log"
"$PY" -u "$RT/logs/_server_rebuild_title_shards.py" >>"$RT/logs/_rebuild_title.nohup.log" 2>&1 || echo "TITLE_REBUILD fail $?" >>"$RT/logs/kick_short_reembed.log"
echo "TITLE_REBUILD done $(date -Iseconds)" >>"$RT/logs/kick_short_reembed.log"
"$PY" -u -m scripts.short_chunk_full_reembed >>"$RT/logs/qwen3_4b_v2_reembed.nohup.log" 2>&1
echo "REEMBED done $(date -Iseconds)" >>"$RT/logs/kick_short_reembed.log"
