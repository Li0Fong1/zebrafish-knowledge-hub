#!/bin/bash
# Zebrafish Hub 守护脚本（放在服务器运行目录，供 setsid 后台启动）
# 作用：Hub 进程一旦退出（静默崩溃 / 被外部信号杀掉），10 秒后自动拉起，
#       避免公网隧道出现 502（症状是网页「打不开/卡住」，但日志里没有 traceback）。
# 启动（务必 setsid，脱离 SSH 会话）：
#   setsid nohup bash /data/zoujin/deng/zebrafish_runtime/hub_supervise.sh >/dev/null 2>&1 &
# 停止：
#   pkill -f hub_supervise.sh; pkill -f 'uvicorn api.api_server:app'
set -u

BASE=/data/zoujin/deng/zebrafish_gene_qa
RT=/data/zoujin/deng/zebrafish_runtime
PY=/data/zoujin/deng/miniconda3/envs/zebrafish/bin/python

export PYTHONPATH="$BASE:$BASE/core:$BASE/database"
# 2026-11-07 与服务器对齐：线上实际是 CUDA_VISIBLE_DEVICES=1（本机仓库曾写成空值，
# 若照仓库重新部署会把 GPU 绑定丢掉，变成抢占 0 号卡）。服务器上的这份为准。
export CUDA_VISIBLE_DEVICES=1
export BGE_RERANKER_PATH="$BASE/models/bge-reranker-base"
export HF_ENDPOINT=https://hf-mirror.com
export PYTHONIOENCODING=utf-8
export PYTHONUTF8=1

mkdir -p "$RT/logs"
echo "[$(date '+%F %T')] supervisor start pid=$$" >> "$RT/logs/hub_supervise.log"

while true; do
  echo "[$(date '+%F %T')] launching uvicorn" >> "$RT/logs/hub_supervise.log"
  cd "$BASE" || exit 1
  # 必须放后台 + wait：① 这样 $! 才有值（set -u 下引用未赋值的 $! 会让脚本直接退出，
  # 旧写法就是这样崩的：uvicorn 一退，监督器跟着死，日志里连 respawn 行都没有）；
  # ② 可以顺手把子进程 pid 写进 hub.pid 供外部查看/回收。
  "$PY" -m uvicorn api.api_server:app --host 127.0.0.1 --port 8080 >> "$RT/logs/hub.log" 2>&1 &
  child=$!
  echo "$child" > "$RT/hub.pid" 2>/dev/null || true
  wait "$child"
  code=$?
  echo "[$(date '+%F %T')] hub exited code=$code, respawn in 10s" >> "$RT/logs/hub_supervise.log"
  sleep 10
done
