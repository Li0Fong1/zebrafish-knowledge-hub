#!/usr/bin/env bash
# ⛔ 已退役（2026-11-07）——不要再部署这个脚本。
#
# 为什么退役：它每轮只认「快速隧道」。一旦接入固定域名（<RT>/cloudflared/config.yml 存在），
# 它会把命名隧道当成「异常进程」杀掉、重建一个随机 trycloudflare 域名 —— 固定域名 2~3 分钟就废。
# 而且它按**文件名** cloudflared_run 匹配进程，同一台机器上出现第二个同名进程时，
# 会把包括生产隧道在内的所有进程一起 kill（实测：公网地址每 2 分钟换一次）。
#
# 现在隧道统一由 hub_guard.sh 管（每 60s 由 systemd timer 触发）：
#   · 模式对齐：config.yml 在/不在 → 自动切命名隧道/快速隧道
#   · 公网自愈：本地 200 + 公网连续 2 轮不通 → 按当前模式重建（命名模式重建的还是命名隧道）
#   · 命名隧道起不来 → 连续 2 次失败自动回退快速隧道，坏配置改名留档
# 原始内容保留在下方，仅供追溯。
# ==============================================================================
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
