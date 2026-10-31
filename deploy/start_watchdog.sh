#!/bin/bash
# 安全重启巡检守护。为什么不把 pkill 和启动命令写在同一行：
#   启动命令里含「hub_watchdog.sh」明文字面量，`pkill -f` 会把这个正在执行命令的 shell
#   一起匹配到 —— 结果 pkill 把自己连坐杀掉，脚本静默没起来（实测踩过，日志里只有 start 行之前的记录）。
# 用法：bash $RT/start_watchdog.sh
set -u
RT=/data/zoujin/deng/zebrafish_runtime
BASE=/data/zoujin/deng/zebrafish_gene_qa

pkill -f 'hub_watchdog[.]sh' 2>/dev/null
sleep 1
cd "$BASE" || exit 1
# 探测间隔默认 120s：比「5 分钟」更勤一点，巡检本身很轻（一次 curl + 几次读文件）。
# 想回到 5 分钟：HUB_WATCH_INTERVAL_SEC=300 bash start_watchdog.sh
HUB_WATCH_INTERVAL_SEC=${HUB_WATCH_INTERVAL_SEC:-120}
export HUB_WATCH_INTERVAL_SEC
setsid nohup bash "$RT/hub_watchdog.sh" </dev/null >/dev/null 2>&1 &
sleep 3
echo "--- 运行中的巡检进程 ---"
pgrep -af 'hub_watchdog' | grep -v 'start_watchdog' || echo "(未启动！)"
echo "--- 最近日志 ---"
tail -3 "$RT/logs/hub_watchdog.log" 2>/dev/null || true
