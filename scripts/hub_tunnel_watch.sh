#!/usr/bin/env bash
# hub_tunnel_watch.sh —— 看护隧道守护本身（2026-10-31）
#
# 为什么需要：hub_tunnel_guard.sh 今天被外部信号打死过一次，而它是纯 bash 循环，
#             自己死了没有任何东西会把它拉回来，公网就再也不会自愈。
# 做法：每 60s 看一次 hub_tunnel_guard.sh 是否在跑，不在就 setsid 拉起来。
# 启动：setsid nohup bash hub_tunnel_watch.sh >/dev/null 2>&1 &
set -u

RT=/data/zoujin/deng/zebrafish_runtime
LOG=$RT/logs/tunnel_watch.log
GUARD=$RT/hub_tunnel_guard.sh
LOCK=$RT/logs/.tunnel_watch.lock

mkdir -p "$RT/logs"
exec 8>"$LOCK" || exit 1
if ! flock -n 8; then
  echo "[$(date '+%F %T')] another watcher holds the lock, exit" >> "$LOG"
  exit 0
fi

echo "[$(date '+%F %T')] watcher start (pid $$)" >> "$LOG"

while true; do
  # 方括号写法避免匹配到 watcher 自己
  n=$(ps -eo args | grep -c '[h]ub_tunnel_guard\.sh')
  if [ "$n" -eq 0 ]; then
    echo "[$(date '+%F %T')] guard not running -> start" >> "$LOG"
    setsid bash "$GUARD" >/dev/null 2>&1 < /dev/null &
    sleep 5
  fi
  sleep 60
done
