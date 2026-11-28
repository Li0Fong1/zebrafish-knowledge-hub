#!/bin/bash
# Zebrafish Hub 五分钟巡检（常驻；root cron 的 hub_guard.sh 已失效时用这个顶上）
#
# 为什么需要它：`hub_guard.sh` 依赖 root cron，而 2026-10-09 起该 cron 已不再执行，
# deng 又没有 crontab 权限。所以这里自带一个「自己每 5 分钟看一次」的循环：
#   1) health != 200            → 杀掉残留进程并重新拉起 supervise（30 秒内应恢复）
#   2) 重启后进程是冷的          → 趁没人用，后台发一个 warm ping，先把重模型拉起来
#   3) 内存吃紧但有人在用        → 只记账（mem_low_busy），不踢人
#   4) 内存吃紧 + 确实空闲       → kill -9 回收，交给 supervise 重启
#   5) 每次巡检都写一行状态快照，便于回溯
#
# 关于 2)：uvicorn 冷启动只加载轻量模型（HUB_WARMUP_RAG=0 省内存），4B 嵌入 + 知识库索引
# 改成首次访问时按需加载 —— 实测第一个用户请求要等 **238s**（热态只要 ~50s）。
# 所以重启后主动发一次请求，把这 4 分钟从「用户等」改成「后台跑」。
#
# 关于 4)：旧版只看 RSS 上限（20GB），但热态本来就会到 18~30GB，于是「刚热起来就被回收」，
# 下次访问又冷启动 4 分钟 —— 得不偿失。现改为：**系统可用内存低于阈值 且 空闲超过 15 分钟**
# 才回收，且回收前先看 hub.log 的 mtime（空闲判据），不踢掉正在提问的人。
#
# 启动（脱离会话）：
#   cd $BASE && setsid nohup bash $RT/hub_watchdog.sh </dev/null >/dev/null 2>&1 &
# 停止：pkill -f hub_watchdog.sh
set -u

BASE=/data/zoujin/deng/zebrafish_gene_qa
RT=/data/zoujin/deng/zebrafish_runtime
LOG="$RT/logs/hub_watchdog.log"
HUB_LOG="$RT/logs/hub.log"
SNAP="$RT/hub_watchdog_last.json"
INTERVAL=${HUB_WATCH_INTERVAL_SEC:-300}      # 5 分钟
RSS_MAX_MB=${HUB_RSS_MAX_MB:-34000}          # 兜底（冷态约 2.5GB，全热态实测 32GB）
MIN_UPTIME=${HUB_MIN_UPTIME_SEC:-600}        # 刚起来的别急着回收
MEM_PCT=${HUB_MEM_PCT:-85}                   # cgroup 匿名内存占用≥此百分比才算「吃紧」（全热态实测约 91%）
IDLE_SEC=${HUB_IDLE_SEC:-300}                # 至少空闲这么久才允许回收
WARM_DELAY=${HUB_WARM_DELAY_SEC:-90}         # 重启后延迟多久发 warm ping
WARM_PING_ON=${HUB_WARM_PING:-1}             # 0 = 关闭自动预热

health() {
  local c
  c=$(curl -s -m 20 -o /dev/null -w "%{http_code}" http://127.0.0.1:8080/api/health 2>/dev/null)
  [ -z "$c" ] && c=000
  echo "$c"
}
worker_pid() { pgrep -f 'uvicorn api[.]api_server' | head -1; }
all_pids()   { pgrep -f 'uvicorn api[.]api_server|hub_supervise[.]sh' | tr '\n' ' '; }
avail_mb()   { free -m 2>/dev/null | awk '/^Mem:/{print $7+$6}'; }
# 真正该看的是 cgroup 占用率；宿主 free 显示 60GB+ 空闲，但我们超出 cgroup 照样被 OOM 杀。
# 坑：进程落在 session-XXXX.scope 里，它自己的 memory.max 是字面量 "max"，
# 真正的上限（36656349184 ≈ 35GB）在祖先 user-1005.slice —— 所以必须往上找。
mem_pct() {
  local hp rel p cur max anon
  hp=$(worker_pid); [ -z "$hp" ] && { echo 0; return; }
  rel=$(awk -F: '/^0:/{print $3}' "/proc/$hp/cgroup" 2>/dev/null)
  [ -z "$rel" ] && { echo 0; return; }
  while [ -n "$rel" ] && [ "$rel" != "/" ]; do
    p="/sys/fs/cgroup$rel"
    max=$(cat "$p/memory.max" 2>/dev/null)
    if [ -n "$max" ] && [ "$max" != "max" ] && [ "$max" -gt 0 ] 2>/dev/null; then
      # 只看匿名内存（进程真实占用）。memory.current 含页缓存，读 58GB 分片后会飙到 99%，
      # 但那部分在内核压力下会自己吐回去，拿它做判据会误报。
      anon=$(awk '/^anon /{print $2}' "$p/memory.stat" 2>/dev/null)
      cur=${anon:-0}
      [ "$cur" -gt 0 ] 2>/dev/null || cur=$(cat "$p/memory.current" 2>/dev/null || echo 0)
      echo $((cur * 100 / max)); return
    fi
    rel=$(dirname "$rel")
  done
  echo 0
}
# 2026-11-12：hub.log 会被 hub_guard 的 /api/health 轮询持续刷新（实测 idle_s 恒为 0），
# 拿它当「有没有真实用户在问答」的判据，会让「内存吃紧 + 空闲」的主动回收永远不触发，
# 最终只能等 cgroup OOM killer 把 Hub 杀掉。改为每轮读 health 的问答状态并累计空闲秒数。
chat_active() {
  local j
  j=$(curl -s -m 8 http://127.0.0.1:8080/api/health 2>/dev/null)
  [ -z "$j" ] && return 1
  echo "$j" | grep -qE '"chat_busy":true|"chat_running":[1-9]|"chat_queued":[1-9]'
}
idle_ticks=0
idle_sec() { echo "$idle_ticks"; }
rss_mb() {
  local hp; hp=$(worker_pid)
  [ -z "$hp" ] && { echo 0; return; }
  awk '/VmRSS/{print int($2/1024)}' "/proc/$hp/status" 2>/dev/null || echo 0
}

# 冷启动兜底：重启后趁没人用，后台跑一次真实请求，把 4B 嵌入 + 知识库索引拉起来，
# 这样用户的第一问不会卡 4 分钟。有人正在访问就让请求自己去加载，我们不抢资源。
# 参数：延迟秒数（默认 WARM_DELAY）。循环里发现冷态时用更短的延迟。
warm_ping() {
  [ "$WARM_PING_ON" = "0" ] && return
  # 2026-11-12：cgroup 已经吃紧时不要再拉 18GB 重模型，否则
  # 「OOM -> 重启 -> warm-ping 拉满 -> 再 OOM」会一直循环。
  if [ "$(mem_pct)" -ge "$MEM_PCT" ]; then
    echo "[$(date '+%F %T')] warm-ping 跳过：cgroup 内存已吃紧（$(mem_pct)% >= ${MEM_PCT}%）" >> "$LOG"
    return
  fi
  local delay=${1:-$WARM_DELAY}
  (
    sleep "$delay"
    cur=$(rss_mb)
    if [ "$cur" -gt 8000 ]; then
      echo "[$(date '+%F %T')] warm-ping 跳过：重模型已被真实请求拉起（rss=${cur}MB）" >> "$LOG"
      exit 0
    fi
    echo "[$(date '+%F %T')] warm-ping 开始（后台加载 4B 嵌入+知识库索引，预计 2~4 分钟）rss=${cur}MB" >> "$LOG"
    # 用专用钩子而不是 /api/chat：对话是全局单并发锁，用 chat 预热会让用户
    # 在预热期间吃到 503「上一问仍在处理中」（2026-10-11 实测的真凶）。
    curl -s -m 900 -X POST -o /dev/null http://127.0.0.1:8080/api/warmup 2>/dev/null
    echo "[$(date '+%F %T')] warm-ping 结束 rss=$(rss_mb)MB" >> "$LOG"
  ) </dev/null >/dev/null 2>&1 &
}

mkdir -p "$RT/logs"
echo "[$(date '+%F %T')] watchdog start pid=$$ interval=${INTERVAL}s rss_max=${RSS_MAX_MB}MB mem_pct=${MEM_PCT}% idle=${IDLE_SEC}s warm_ping=${WARM_PING_ON}" >> "$LOG"

# watchdog 自己重启时，服务可能已经是冷的（内存被回收过）→ 同样补一次预热
[ "$(rss_mb)" -lt 8000 ] && warm_ping

while true; do
  TS=$(date '+%F %T')
  code=$(health)
  action=none

  if [ -f "$RT/HUB_STOPPED" ]; then
    action=stopped_by_sentinel
  elif [ "$code" != "200" ]; then
    echo "[$TS] health=$code -> respawn supervise" >> "$LOG"
    for p in $(all_pids); do kill -9 "$p" 2>/dev/null; done
    sleep 2
    cd "$BASE" || exit 1
    setsid nohup bash "$RT/hub_supervise.sh" </dev/null >/dev/null 2>&1 &
    action=respawn
    for i in $(seq 1 8); do
      sleep 15
      code=$(health)
      [ "$code" = "200" ] && break
    done
    echo "[$TS] respawn done health=$code after $((i*15))s" >> "$LOG"
    warm_ping
  else
    hp=$(worker_pid)
    if [ -n "$hp" ]; then
      cur_rss=$(rss_mb)
      up_s=$(ps -o etimes= -p "$hp" 2>/dev/null | tr -d ' ')
      mpct=$(mem_pct)
      if chat_active; then idle_ticks=0; else idle_ticks=$((idle_ticks + INTERVAL)); fi
      idle=$idle_ticks
      up_s=${up_s:-0}
      # 兜底：单进程 RSS 超过 32GB（已经不可能，但留着防手滑改配置）
      if [ "$cur_rss" -gt $((RSS_MAX_MB * 1024 / 1024)) ]; then
        action=over_rss_limit
      fi
      # 回收条件（缺一不可）：cgroup 内存吃紧 + 空闲 ≥ IDLE_SEC + 跑够久了
      if [ "$mpct" -ge "$MEM_PCT" ] && [ "$up_s" -gt "$MIN_UPTIME" ] && [ "$idle" -gt "$IDLE_SEC" ]; then
        echo "[$TS] recycle: cgroup=${mpct}% rss=${cur_rss}MB idle=${idle}s up=${up_s}s -> kill -9" >> "$LOG"
        kill -9 "$hp" 2>/dev/null
        action=recycle_mem
      elif [ "$mpct" -ge "$MEM_PCT" ]; then
        action=mem_busy_keep      # 内存紧但有人在用：只记账，不踢人
      fi

      # 冷态兜底（关键）：手工 kill / 崩溃后 supervise 10s 自愈，health 全程 200，
      # 走不到 respawn 分支 → 以前就没人预热，用户第一问仍然卡 4 分钟（实测踩过）。
      # 这里用「该 pid 是否已被预热过」当标记，避免每轮重复预热。
      if [ "$cur_rss" -lt 8000 ] && [ "$up_s" -gt 120 ]; then
        marker="$RT/.warmed_pid"
        if [ "$(cat "$marker" 2>/dev/null)" != "$hp" ]; then
          echo "$hp" > "$marker"
          echo "[$TS] cold detected rss=${cur_rss}MB up=${up_s}s -> warm ping" >> "$LOG"
          action=warm_ping
          warm_ping 15
        fi
      fi
    fi
  fi

  printf '{"ts":"%s","health":"%s","action":"%s","rss_mb":%s,"mem_pct":%s,"idle_s":%s,"supervise":%s}\n' \
    "$TS" "$code" "$action" "$(rss_mb)" "$(mem_pct)" "$(idle_sec)" \
    "$(pgrep -c -f 'hub_supervise[.]sh' 2>/dev/null || true)" > "$SNAP"

  sleep "$INTERVAL"
done
