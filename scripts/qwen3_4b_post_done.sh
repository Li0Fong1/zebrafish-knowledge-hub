#!/bin/bash
# After Qwen3-4B reembed reaches ready: register shards + leave switch note (do NOT auto-switch Hub).
set -u
RUNTIME=/data/zoujin/deng/zebrafish_runtime
BASE=/data/zoujin/deng/zebrafish_gene_qa
PY=/data/zoujin/deng/miniconda3/envs/zebrafish/bin/python
LOG=$RUNTIME/logs/qwen3_4b_post_done.log
STATUS=$RUNTIME/logs/qwen3_4b_status.json
MF=$RUNTIME/faiss_pubmed_yes_qwen3_4b_v1/manifest.json
MARKER=$RUNTIME/logs/qwen3_4b_post_done.done

exec >>"$LOG" 2>&1
echo "=== $(date -Is) post_done check ==="

if [ -f "$MARKER" ]; then
  echo already_done
  exit 0
fi

if [ ! -f "$MF" ]; then
  echo no_manifest
  exit 1
fi

ready=$($PY - <<'PY'
import json
m=json.load(open("/data/zoujin/deng/zebrafish_runtime/faiss_pubmed_yes_qwen3_4b_v1/manifest.json"))
s={}
try:
  s=json.load(open("/data/zoujin/deng/zebrafish_runtime/logs/qwen3_4b_status.json"))
except Exception:
  pass
print("1" if (m.get("status")=="ready" or s.get("phase")=="done") else "0")
print(m.get("total_papers"), m.get("total_chunks"), len(m.get("shards") or []))
PY
)
ok=$(echo "$ready" | head -n1)
meta=$(echo "$ready" | tail -n1)
echo meta=$meta ok=$ok
if [ "$ok" != "1" ]; then
  echo not_ready_yet
  exit 2
fi

source /data/zoujin/deng/miniconda3/etc/profile.d/conda.sh
conda activate zebrafish
cd "$BASE"
export PYTHONPATH="$BASE:$BASE/core:$BASE/database"
export FAISS_PUBMED_YES_DIR=$RUNTIME/faiss_pubmed_yes_qwen3_4b_v1

# register 4B shards if script supports path/version args
$PY -u -m scripts.register_gte_shards_to_db --index-version qwen3_4b_v1 \
  || $PY - <<'PY'
import os, sys
from pathlib import Path
sys.path[:0]=["/data/zoujin/deng/zebrafish_gene_qa","/data/zoujin/deng/zebrafish_gene_qa/core","/data/zoujin/deng/zebrafish_gene_qa/database"]
os.chdir("/data/zoujin/deng/zebrafish_gene_qa")
from core.entry import bootstrap_app; bootstrap_app()
from literature_store import upsert_vector_shard, start_job, finish_job, write_sync_log
from mysql_client import MySQLClient
import json
mf=Path("/data/zoujin/deng/zebrafish_runtime/faiss_pubmed_yes_qwen3_4b_v1/manifest.json")
m=json.loads(mf.read_text())
client=MySQLClient(); db=next(client.get_db())
job=start_job(db, job_type="register_qwen3_4b_shards", params={"dir": str(mf.parent)})
db.commit()
n=0
for sid, info in (m.get("shard_info") or {}).items():
  upsert_vector_shard(db, {
    "index_version":"qwen3_4b_v1",
    "shard_id": int(sid),
    "paper_count": int(info.get("paper_count") or 0),
    "chunk_count": int(info.get("chunk_count") or 0),
    "kind": info.get("kind") or "",
    "path": f"/data/zoujin/deng/zebrafish_runtime/faiss_pubmed_yes_qwen3_4b_v1/shard_{int(sid):04d}",
  })
  n+=1
finish_job(db, job, status="success", result={"shards": n})
write_sync_log(db, action="register_qwen3_4b_shards", detail=f"n={n}")
db.commit()
print("registered", n)
PY

cat > $RUNTIME/qwen3_4b_switch_ready.txt <<EOF
Qwen3-4B index READY (do NOT auto-switch Hub yet)
path: $RUNTIME/faiss_pubmed_yes_qwen3_4b_v1
dim: 2048
meta: $meta
gte remains: $RUNTIME/faiss_pubmed_yes_gte_mbase_v1
To switch Hub manually after smoke:
  EMBEDDING_MODEL_PATH=.../models/Qwen3-Embedding-4B
  FAISS_PUBMED_YES_DIR=.../faiss_pubmed_yes_qwen3_4b_v1
EOF

date -Is > "$MARKER"
echo POST_DONE_OK
