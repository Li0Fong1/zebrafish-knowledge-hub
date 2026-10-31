#!/usr/bin/env bash
set -euo pipefail
RT=/data/zoujin/deng/zebrafish_runtime
BASE=/data/zoujin/deng/zebrafish_gene_qa
PY=/data/zoujin/deng/miniconda3/envs/zebrafish/bin/python
cd "$BASE"
export ZEBRA_RUNTIME=$RT ZEBRA_BASE=$BASE PROJECT=$BASE PYTHONPATH=$BASE
export SCIHUB_PARSE_LOG=$RT/logs/scihub_gold_nohup.log
export GOLD_OUT=$RT/gold_ingest/yes_fulltext_gold
export SCIHUB_INBOX=$RT/gold_ingest/pdfku_inbox/scihub_batch
export FAISS_QWEN3_4B_DIR=$RT/faiss_pubmed_yes_qwen3_4b_v1
export QWEN3_4B_DIR=$RT/models/Qwen3-Embedding-4B

echo "=== ACCEPT ==="
$PY -u -m scripts.three_day_closeout --accept-only
echo "=== AUDIT ==="
$PY -u -m scripts.full_compliance_audit
python3 - <<'PY'
import json
from pathlib import Path
S=Path("/data/zoujin/deng/zebrafish_runtime/logs/scheduler")
acc=json.loads((S/"accept_latest.json").read_text())
rep=json.loads((S/"full_compliance_report.json").read_text())
print("ACCEPT_PASS", acc.get("pass"))
print("counts", acc.get("counts"))
print("AUDIT_PASS", rep.get("pass"))
print("summary", rep.get("summary"))
PY
