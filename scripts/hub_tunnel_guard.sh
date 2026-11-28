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
# hub_tunnel_guard.sh —— cloudflared quick tunnel 守护（2026-10-30 建，2026-10-31 加固）
#
# 背景：quick tunnel 的凭证会被 Cloudflare 侧回收（日志报 "Unauthorized: Tunnel not found"），
#       此时进程还活着但公网 530，必须重建隧道才能拿到新地址。
# 做法：每 60s 检查公网 /api/health；连续 2 次失败且本地后端正常 -> 重建隧道并刷新地址文件。
#
# 2026-10-31 加固：
#   1) flock 单实例，避免重复拉起造成多隧道互相顶掉；
#   2) 每 10 轮打一条 heartbeat，用来证明守护还活着（旧版只在异常时写日志，
#      结果「日志很久没动」既可能是安静运行也可能已经死了，分不清）；
#   3) 重建后立刻回写 tunnel_url.txt（原子替换），并记录耗时。
# 注意：pgrep -f 会匹配到执行自身命令的 bash，一律用 ps+awk 过滤 comm。
set -u

RT=/data/zoujin/deng/zebrafish_runtime
LOG=$RT/logs/tunnel_guard.log
URLF=$RT/tunnel_url.txt
QLOG=/tmp/cf_quick.log
LOCK=$RT/logs/.tunnel_guard.lock
INTERVAL=60

mkdir -p "$RT/logs"

# —— 单实例锁：拿不到就退出，绝不起第二个 ——
exec 9>"$LOCK" || exit 1
if ! flock -n 9; then
  echo "[$(date '+%F %T')] another guard holds the lock, exit" >> "$LOG"
  exit 0
fi

find_pids() {
  ps -eo pid,comm,args \
    | awk '/cloudflared_run tunnel/ && $2!="bash" && $2!="awk" && $2!="sh" && $2!="ps" {print $1}'
}

local_health() { timeout 10 curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/api/health 2>/dev/null; }

start_tunnel() {
  local t0 u i
  t0=$(date +%s)
  mv -f "$QLOG" "$QLOG.old_$(date +%s)" 2>/dev/null
  setsid "$RT/bin/cloudflared_run" tunnel --no-autoupdate --protocol http2 \
      --url http://127.0.0.1:8080 > "$QLOG" 2>&1 < /dev/null &
  for i in $(seq 1 40); do
    sleep 3
    u=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$QLOG" 2>/dev/null | tail -1)
    if [ -n "$u" ]; then
      echo "$u" > "$URLF.tmp" && mv -f "$URLF.tmp" "$URLF"
      echo "[$(date '+%F %T')] new-url $u (took $(( $(date +%s) - t0 ))s)" >> "$LOG"
      return 0
    fi
  done
  echo "[$(date '+%F %T')] ERROR start-timeout" >> "$LOG"
  return 1
}

echo "[$(date '+%F %T')] guard start (pid $$)" >> "$LOG"
[ -s "$URLF" ] || start_tunnel

fail=0
tick=0
while true; do
  tick=$((tick + 1))

  n=$(find_pids | wc -l)
  if [ "$n" -ne 1 ]; then
    echo "[$(date '+%F %T')] WARN pid-count=$n resetting" >> "$LOG"
    for p in $(find_pids); do kill -9 "$p" 2>/dev/null; done
    sleep 1
    start_tunnel
    fail=0
    sleep "$INTERVAL"
    continue
  fi

  lc=$(local_health)
  if [ "$lc" != "200" ]; then
    # 后端自己不可用，重启隧道没有意义（也可能是正在加载模型）
    echo "[$(date '+%F %T')] SKIP backend=$lc" >> "$LOG"
    sleep "$INTERVAL"
    continue
  fi

  u=$(cat "$URLF" 2>/dev/null)
  code=$(timeout 25 curl -s -o /dev/null -w '%{http_code}' "$u/api/health" 2>/dev/null)
  if [ "$code" = "200" ]; then
    [ "$fail" -ne 0 ] && echo "[$(date '+%F %T')] recovered" >> "$LOG"
    fail=0
  else
    fail=$((fail + 1))
    echo "[$(date '+%F %T')] WARN public=$code fail=$fail url=$u" >> "$LOG"
    if [ "$fail" -ge 2 ]; then
      for p in $(find_pids); do kill -9 "$p" 2>/dev/null; done
      sleep 1
      start_tunnel
      fail=0
    fi
  fi

  # 每 10 轮（约 10 分钟）留一条心跳，证明守护在工作
  if [ $((tick % 10)) -eq 0 ]; then
    echo "[$(date '+%F %T')] heartbeat ok url=$u" >> "$LOG"
  fi

  sleep "$INTERVAL"
done
