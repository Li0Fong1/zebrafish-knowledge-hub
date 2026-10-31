#!/bin/bash
# 冷启动自愈 + 后台预热验证（一次性脚本，跑完看 $RT/logs/warm_verify.log）
# 流程：kill -9 uvicorn → 每 20s 记录 health / RSS / cgroup%，连续 10 分钟
# 预期：~10s 内 health 恢复 200；~90s 后 watchdog 发 warm ping，RSS 在无人访问的情况下
#       自己从 ~2.5GB 涨到 15GB+（说明重模型已提前加载，用户第一问不用等 4 分钟）
set -u
RT=/data/zoujin/deng/zebrafish_runtime
OUT="$RT/logs/warm_verify.log"
: > "$OUT"
sample() {
  local hp h r pct dir
  h=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 http://127.0.0.1:8080/api/health 2>/dev/null)
  hp=$(pgrep -f 'uvicorn api[.]api_server' | head -1)
  if [ -n "$hp" ]; then
    r=$(awk '/VmRSS/{print int($2/1024)}' "/proc/$hp/status" 2>/dev/null)
    dir="/sys/fs/cgroup$(awk -F: '/^0:/{print $3}' /proc/$hp/cgroup 2>/dev/null)"
    pct=$(awk -v c="$(cat "$dir/memory.current" 2>/dev/null || echo 0)" \
              -v m="$(cat "$dir/memory.max" 2>/dev/null || echo 1)" \
              'BEGIN{ if (m+0>0) printf "%d", c*100/m; else print 0 }')
  else
    r=0; pct=0
  fi
  echo "[$(date '+%T')] health=$h rss=${r:-0}MB cgroup=${pct:-0}%" >> "$OUT"
}
echo "=== 起始状态 ===" >> "$OUT"; sample
pkill -9 -f 'uvicorn api[.]api_server'
echo "=== 已 kill uvicorn，开始采样 ===" >> "$OUT"
for i in $(seq 1 30); do
  sleep 20; sample
done
echo "=== 采样结束 ===" >> "$OUT"
