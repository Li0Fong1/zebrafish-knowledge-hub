#!/bin/bash
# Zebrafish Hub + 公网隧道 存活守护（crontab 每分钟跑一次）
#
# 为什么需要它：
#   这台机器是共享的，load average 会在 1 与 118 之间来回跳，Swap 8191/8191 全满；
#   内存压力下发生过 cgroup 级清理，把 uvicorn **和** 它的 nohup/setsid 守护脚本一起杀掉，
#   日志里没有 traceback，外部表现为 Cloudflare 502/530/1033。cron 任务生命周期短，
#   不容易被一起清理，所以用它来做复活。
#
# 另外（2026-10-10 添加）：省资源档 `HUB_WARMUP_RAG=0` 是「按需加载」，4B 嵌入模型与
#   FAISS 分片一旦被访问就常驻，一天后 RSS 会涨回 30 GB+，逼近 cgroup 上限 36 GB。
#   因此这里加了「内存回收」：RSS > HUB_RSS_MAX_MB（默认 20000）且进程已运行
#   > HUB_MIN_UPTIME_SEC（默认 600 s）时，kill -9 让它重启（空闲 RSS 约 2.5 GB）。
#
# 安装（幂等）：
#   crontab -l | grep -v hub_guard.sh > /tmp/ct
#   echo "* * * * * /data/zoujin/deng/zebrafish_runtime/hub_guard.sh >>/data/zoujin/deng/zebrafish_runtime/logs/hub_guard.log 2>&1" >> /tmp/ct
#   crontab /tmp/ct
#
# 状态快照（每次覆盖写）：$RT/hub_guard_last.json
#
# 公网隧道两种模式（2026-10-16 添加命名隧道支持，自动识别，互不影响）：
#   quick（默认）：`tunnel --url http://127.0.0.1:8080` —— 匿名随机域名，重启就换，
#                  域名只能从日志里抓，所以下面要写 $RT/public_url.txt。
#   named（固定域名）：$RT/cloudflared/config.yml 存在即启用 —— `tunnel --config … run <名字>`，
#                  域名来自 config.yml 的 `hostname:`，**永不变**，以后不再需要抓日志。
#   切换方式：把 config.yml 放到 $RT/cloudflared/ 下，本脚本下一分钟自动切成命名隧道；
#   删掉该文件即退回快速隧道。生成配置用 `scripts/setup_named_tunnel.py`。
set -u

BASE=/data/zoujin/deng/zebrafish_gene_qa
RT=/data/zoujin/deng/zebrafish_runtime
# cloudflared 客户端优先用持久目录 $RT/bin（2026-10-16 起）——旧的 /tmp/cloudflared_run
# 在服务器重启后会被清掉，守护会一直报 missing_binary。两边都叫 cloudflared_run：
# cf_pids() 靠这个名字精确匹配，避免误杀共享机器上别人的 cloudflared 进程。
CF_BIN="$RT/bin/cloudflared_run"
[ -x "$CF_BIN" ] || CF_BIN=/tmp/cloudflared_run
CF_NAMED_CONF="$RT/cloudflared/config.yml"
CF_NAMED_NAME_FILE="$RT/cloudflared/tunnel_name.txt"
CF_URL_FILE="$RT/public_url.txt"
TS=$(date '+%F %T')

log() { echo "[$TS] $*"; }
health() {
  local c
  c=$(curl -s -m 20 -o /dev/null -w "%{http_code}" http://127.0.0.1:8080/api/health 2>/dev/null)
  [ -z "$c" ] && c=000
  echo "$c"
}
cf_pids() { ps -eo pid,cmd | grep -E "cloudflared_run tunnel" | grep -v grep | awk '{print $1}'; }
hub_pids() { ps -eo pid,cmd | grep -E "uvicorn api[.]api_server:app|hub_supervise[.]sh" | grep -v grep | awk '{print $1}'; }
hub_worker_pid() { ps -eo pid,cmd | grep -E "uvicorn api[.]api_server:app" | grep -v grep | awk '{print $1}' | head -1; }

# —— 停止开关（2026-10-09 添加）：存在 $RT/HUB_STOPPED 时，本守护不再复活 Hub 与公网隧道 ——
if [ -f "$RT/HUB_STOPPED" ]; then
  printf '{"ts":"%s","skipped":"HUB_STOPPED"}\n' "$TS" > "$RT/hub_guard_last.json"
  exit 0
fi

# 防重入：cron 与手动/上一次未跑完的实例不要同时重启
exec 9>"$RT/hub_guard.lock"
if command -v flock >/dev/null 2>&1; then
  flock -n 9 || exit 0
fi

HUB_ACTION=none
CF_ACTION=none

# —— 1. Hub ——
code=$(health)
if [ "$code" != "200" ]; then
  log "hub health=$code -> restart"
  for p in $(hub_pids); do kill -9 "$p" 2>/dev/null; done
  sleep 2
  cd "$BASE" || exit 1
  setsid nohup bash "$RT/hub_supervise.sh" >/dev/null 2>&1 &
  HUB_ACTION=restart
  for i in $(seq 1 8); do
    sleep 15
    code=$(health)
    [ "$code" = "200" ] && break
  done
  log "hub restart done health=$code after $((i*15))s"
fi

# —— 1.5 内存回收（2026-10-10 添加）——
# 健康但 RSS 已经很大时主动重启，把常驻的嵌入模型/索引分片放掉；
# 阈值可用 HUB_RSS_MAX_MB / HUB_MIN_UPTIME_SEC 覆盖。
HUB_RSS_MAX_MB=${HUB_RSS_MAX_MB:-20000}
HUB_MIN_UPTIME_SEC=${HUB_MIN_UPTIME_SEC:-600}
if [ "$code" = "200" ]; then
  hp=$(hub_worker_pid)
  if [ -n "$hp" ]; then
    rss_kb=$(awk '/VmRSS/{print $2}' "/proc/$hp/status" 2>/dev/null)
    up_s=$(ps -o etimes= -p "$hp" 2>/dev/null | tr -d ' ')
    rss_kb=${rss_kb:-0}
    up_s=${up_s:-0}
    if [ "$rss_kb" -gt $((HUB_RSS_MAX_MB * 1024)) ] && [ "$up_s" -gt "$HUB_MIN_UPTIME_SEC" ]; then
      log "hub rss=$((rss_kb/1024))MB > ${HUB_RSS_MAX_MB}MB (up ${up_s}s) -> recycle"
      for p in $(hub_pids); do kill -9 "$p" 2>/dev/null; done
      sleep 2
      cd "$BASE" || exit 1
      setsid nohup bash "$RT/hub_supervise.sh" >/dev/null 2>&1 &
      HUB_ACTION=recycle_mem
      for i in $(seq 1 8); do
        sleep 15
        code=$(health)
        [ "$code" = "200" ] && break
      done
      log "hub recycle done health=$code after $((i*15))s"
    fi
  fi
fi

# —— 2. 公网隧道（quick = 随机域名，重启会变；named = 固定域名） ——
CF_MODE=quick
[ -f "$CF_NAMED_CONF" ] && CF_MODE=named

if [ ! -x "$CF_BIN" ]; then
  CF_ACTION=missing_binary
elif [ -z "$(cf_pids)" ]; then
  if [ "$CF_MODE" = "named" ]; then
    # —— 命名隧道：域名固定，只需保证进程在、且已向 Cloudflare 注册 ——
    cf_name=$(tr -d ' \r\n' < "$CF_NAMED_NAME_FILE" 2>/dev/null)
    [ -z "$cf_name" ] && cf_name=$(awk '/^tunnel:/{print $2; exit}' "$CF_NAMED_CONF" 2>/dev/null | tr -d ' \r')
    log "cloudflared down -> restart (named tunnel: ${cf_name:-?})"
    before=$(grep -c "Registered tunnel connection" "$RT/logs/cloudflared_hub.log" 2>/dev/null || echo 0)
    setsid nohup "$CF_BIN" tunnel --no-autoupdate --config "$CF_NAMED_CONF" run "$cf_name" \
      >> "$RT/logs/cloudflared_hub.log" 2>&1 &
    CF_ACTION=restart
    # 日志是追加写的，所以要比“新增行数”而不是“存在即算好”
    for i in $(seq 1 8); do
      sleep 5
      now=$(grep -c "Registered tunnel connection" "$RT/logs/cloudflared_hub.log" 2>/dev/null || echo 0)
      [ "$now" -gt "$before" ] && break
    done
    log "cloudflared restarted (named) registered=${now:-0}"
  else
    log "cloudflared down -> restart"
    setsid nohup "$CF_BIN" tunnel --no-autoupdate --protocol http2 --url http://127.0.0.1:8080 \
      >> "$RT/logs/cloudflared_hub.log" 2>&1 &
    CF_ACTION=restart
    url=""
    for i in $(seq 1 8); do
      sleep 10
      url=$(grep -oE "https://[a-z0-9-]+[.]trycloudflare[.]com" "$RT/logs/cloudflared_hub.log" 2>/dev/null | tail -1)
      [ -n "$url" ] && break
    done
    log "cloudflared restarted url=$url"
  fi
fi

if [ "$CF_MODE" = "named" ]; then
  # 域名写死在 config.yml 里，直接取，不用（也取不到）去日志里捞
  cf_host=$(awk '/hostname:/{print $2; exit}' "$CF_NAMED_CONF" 2>/dev/null | tr -d ' \r')
  [ -n "$cf_host" ] && echo "https://$cf_host" > "$CF_URL_FILE"
else
  url=$(grep -oE "https://[a-z0-9-]+[.]trycloudflare[.]com" "$RT/logs/cloudflared_hub.log" 2>/dev/null | tail -1)
  [ -n "$url" ] && echo "$url" > "$CF_URL_FILE"
fi

# —— 3. 状态快照（不刷日志，方便随时查看） ——
url_now=$(cat "$CF_URL_FILE" 2>/dev/null || echo "")
printf '{"ts":"%s","hub_health":"%s","hub_pids":"%s","hub_action":"%s","cf_mode":"%s","cf_action":"%s","public_url":"%s"}\n' \
  "$TS" "$code" "$(hub_pids | tr '\n' ',')" "$HUB_ACTION" "$CF_MODE" "$CF_ACTION" "$url_now" > "$RT/hub_guard_last.json"

exit 0
