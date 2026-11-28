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
#
# 2026-11-07 修两个会「让站点变黑」的缺陷：
#   ① 模式切换从来没生效过。老版本只在「没有隧道进程」时才动进程，而 config.yml 出现 /
#      消失只改了「应然」，进程还跑在旧模式里；更糟的是 public_url.txt 照旧按应然写，
#      于是文件上写着固定域名、实际流量还在随机域名上（--revert 同样是假回退）。
#      现在改为**从进程命令行反推实际模式**，与应然不一致就重启进程对齐。
#   ② 命名隧道起不来时无限重试。凭据错、隧道被删、域名没绑，都会让 8080 永远没入口。
#      现在命名隧道连续失败 2 次就自动回退快速隧道（config.yml 改名留档），宁可地址变了，
#      也不能整个站点失联。
set -u

# 允许用环境变量覆盖，便于在临时目录里做「桩测试」而不碰生产（见 scripts/test_hub_guard.py）
BASE=${HUB_BASE:-/data/zoujin/deng/zebrafish_gene_qa}
RT=${HUB_RT:-/data/zoujin/deng/zebrafish_runtime}
# cloudflared 客户端优先用持久目录 $RT/bin（2026-10-16 起）——旧的 /tmp/cloudflared_run
# 在服务器重启后会被清掉，守护会一直报 missing_binary。两边都叫 cloudflared_run：
# cf_pids() 靠这个名字精确匹配，避免误杀共享机器上别人的 cloudflared 进程。
# HUB_CF_BIN 只为桩测试准备：测试用一个不同文件名的假二进制，就不会和线上进程撞名，
# 也不会被历史遗留的按名字匹配的脚本当成「多出来的隧道」而一起清掉。
CF_BIN="${HUB_CF_BIN:-$RT/bin/cloudflared_run}"
[ -x "$CF_BIN" ] || [ -n "${HUB_CF_BIN:-}" ] || CF_BIN=/tmp/cloudflared_run
CF_DIR="$RT/cloudflared"
CF_NAMED_CONF="$CF_DIR/config.yml"
CF_NAMED_NAME_FILE="$CF_DIR/tunnel_name.txt"
CF_URL_FILE="$RT/public_url.txt"
TS=$(date '+%F %T')

log() { echo "[$TS] $*"; }
health() {
  local c
  c=$(curl -s -m 20 -o /dev/null -w "%{http_code}" http://127.0.0.1:8080/api/health 2>/dev/null)
  [ -z "$c" ] && c=000
  echo "$c"
}
# 只认「我们自己的那个二进制绝对路径」。老版本按名字 cloudflared_run 匹配，这在共享机器上
# 有两个坏处：别人的同名程序会被误杀；而且在临时目录里做桩测试时，测试进程会被一起杀掉，
# 反过来测试也可能把生产隧道杀掉。按 $CF_BIN 绝对路径匹配就没有这些歧义。
cf_lines() { ps -eo pid,args | grep -F "$CF_BIN" | grep -v grep | grep -F " tunnel"; }
cf_pids() { cf_lines | awk '{print $1}'; }
hub_pids() { ps -eo pid,cmd | grep -E "uvicorn api[.]api_server:app|hub_supervise[.]sh" | grep -v grep | awk '{print $1}'; }
hub_worker_pid() { ps -eo pid,cmd | grep -E "uvicorn api[.]api_server:app" | grep -v grep | awk '{print $1}' | head -1; }

# —— 停止开关（2026-10-09 添加）：存在 $RT/HUB_STOPPED 时，本守护不再复活 Hub 与公网隧道 ——
if [ -f "$RT/HUB_STOPPED" ]; then
  printf '{"ts":"%s","skipped":"HUB_STOPPED"}\n' "$TS" > "$RT/hub_guard_last.json"
  exit 0
fi

# 防重入：cron 与手动/上一次未跑完的实例不要同时重启。
# 坑（2026-11-07 修）：fd 9 会被 setsid 拉起的子进程继承。守护一旦重启过隧道，
# cloudflared 就永久握着这把锁，之后每一轮 flock -n 都失败并**静默退出** ——
# 表现就是「守护跑了一次就再也不干活」。所以下面每个 setsid 都加了 9>&- 关掉这个 fd。
# 2026-11-07：锁文件换了路径（$RT/locks/ 下）。老路径 hub_guard.lock 已经被**旧版守护
# 遗留下来的进程**永久占用着（PID 2980000 hub_supervise 与它拉起的 uvicorn 都继承着那个 fd，
# 因为老版启动子进程时没关 fd 9）。不换路径就得为了清锁重启一次 Hub，没必要。
HUB_GUARD_LOCK=${HUB_GUARD_LOCK:-$RT/locks/hub_guard.lock}
mkdir -p "$(dirname "$HUB_GUARD_LOCK")" 2>/dev/null
exec 9>"$HUB_GUARD_LOCK"
if command -v flock >/dev/null 2>&1; then
  flock -n 9 || exit 0
fi

HUB_ACTION=none
CF_ACTION=none

# —— 1. Hub ——
# 2026-11-07：Hub 的复活/预热/内存回收交给 hub_watchdog.sh（systemd 常驻、Restart=always），
# 本脚本置 HUB_GUARD_HUB_RESTART=0 后只管隧道。两个脚本同时 kill+重启 Hub 会互相踩，
# 反而把「重启中」的进程反复打断。默认仍是 1，直接单独跑这个脚本时行为不变。
code=$(health)
if [ "$code" != "200" ] && [ "${HUB_GUARD_HUB_RESTART:-1}" != "0" ]; then
  log "hub health=$code -> restart"
  for p in $(hub_pids); do kill -9 "$p" 2>/dev/null; done
  sleep 2
  cd "$BASE" || exit 1
  setsid nohup bash "$RT/hub_supervise.sh" >/dev/null 2>&1 9>&- &
  HUB_ACTION=restart
  for i in $(seq 1 8); do
    sleep 15
    code=$(health)
    [ "$code" = "200" ] && break
  done
  log "hub restart done health=$code after $((i*15))s"
fi

# —— 1.5 内存回收（2026-10-10 添加；2026-11-07 加开关）——
# 健康但 RSS 已经很大时主动重启，把常驻的嵌入模型/索引分片放掉；
# 阈值可用 HUB_RSS_MAX_MB / HUB_MIN_UPTIME_SEC 覆盖。
#
# 注意：本脚本改成每分钟跑一次之后，这条「RSS>20GB 就重启」的判据会误伤热态 Hub
# —— 实测热态 RSS 本来就在 18~30GB，于是刚预热好就被回收，用户下次提问又要等 4 分钟。
# 内存策略统一交给 hub_watchdog.sh（它看 cgroup 匿名内存 + 空闲时长，判据更准），
# 本脚本置 HUB_GUARD_MEM_RECYCLE=0 关掉这一段。
HUB_GUARD_MEM_RECYCLE=${HUB_GUARD_MEM_RECYCLE:-1}
HUB_RSS_MAX_MB=${HUB_RSS_MAX_MB:-20000}
HUB_MIN_UPTIME_SEC=${HUB_MIN_UPTIME_SEC:-600}
if [ "$code" = "200" ] && [ "$HUB_GUARD_MEM_RECYCLE" != "0" ]; then
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
      setsid nohup bash "$RT/hub_supervise.sh" >/dev/null 2>&1 9>&- &
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
CF_FAIL_FILE="$CF_DIR/.cf_named_fails"      # 命名隧道「连续」启动失败次数
CF_NAMED_MAX_FAIL=${HUB_CF_NAMED_MAX_FAIL:-2}

# 实际模式必须从进程命令行反推：配置文件只是「应然」，进程才是「实然」。
# 反推的好处是无需状态文件，老进程也能正确识别（不会误判一次、白换一次随机域名）。
cf_running_mode() {
  local line
  line=$(cf_lines | head -1)
  [ -z "$line" ] && return 0
  case "$line" in
    *--config*) echo named ;;
    *)          echo quick ;;
  esac
}

cf_name_of() {
  local n
  n=$(tr -d ' \r\n' < "$CF_NAMED_NAME_FILE" 2>/dev/null)
  [ -z "$n" ] && n=$(awk '/^tunnel:/{print $2; exit}' "$CF_NAMED_CONF" 2>/dev/null | tr -d ' \r')
  echo "$n"
}

quick_url_latest() {
  # 线上快速隧道可能由两个来源启动（本脚本 / 历史遗留的 hub_tunnel_guard），日志位置不同：
  # 只看其中一份会读到过期地址，进而误判「公网不通」并重建隧道 —— 而重建就会换随机域名。
  local f mt best="" best_mt=0 u
  for f in "$RT/logs/cloudflared_hub.log" /tmp/cf_quick.log; do
    [ -f "$f" ] || continue
    mt=$(stat -c %Y "$f" 2>/dev/null || echo 0)
    if [ "$mt" -ge "$best_mt" ]; then
      u=$(grep -oE "https://[a-z0-9-]+[.]trycloudflare[.]com" "$f" 2>/dev/null | tail -1)
      [ -n "$u" ] && { best=$u; best_mt=$mt; }
    fi
  done
  [ -z "$best" ] && best=$(tr -d ' \r\n' < "$RT/tunnel_url.txt" 2>/dev/null)
  echo "$best"
}

start_quick_tunnel() {
  setsid nohup "$CF_BIN" tunnel --no-autoupdate --protocol http2 --url http://127.0.0.1:8080 \
    >> "$RT/logs/cloudflared_hub.log" 2>&1 </dev/null 9>&- &
  CF_ACTION=restart
  local url="" i
  for i in $(seq 1 8); do
    sleep 10
    url=$(quick_url_latest)
    [ -n "$url" ] && break
  done
  log "cloudflared started (quick) url=$url"
}

# 返回 0 = 已向 Cloudflare 注册成功；1 = 失败
start_named_tunnel() {
  local cf_name before now i
  cf_name=$(cf_name_of)
  before=$(grep -c "Registered tunnel connection" "$RT/logs/cloudflared_hub.log" 2>/dev/null); before=${before:-0}
  log "cloudflared starting (named tunnel: ${cf_name:-?})"
  setsid nohup "$CF_BIN" tunnel --no-autoupdate --config "$CF_NAMED_CONF" run "$cf_name" \
    >> "$RT/logs/cloudflared_hub.log" 2>&1 </dev/null 9>&- &
  CF_ACTION=restart
  # 日志是追加写的，所以要比「新增行数」而不是「存在即算好」
  now=$before
  for i in $(seq 1 8); do
    sleep 5
    now=$(grep -c "Registered tunnel connection" "$RT/logs/cloudflared_hub.log" 2>/dev/null); now=${now:-0}
    [ "$now" -gt "$before" ] && { log "cloudflared started (named) registered=$now"; return 0; }
  done
  log "cloudflared named tunnel NOT registered after 40s (name=${cf_name:-?}, conf=$CF_NAMED_CONF)"
  return 1
}

# 回退：把 config.yml 改名留档 → 守护下一轮看到「应然=quick」自动起快速隧道。
# 这里直接起，不等到下一轮，尽量缩短断网窗口。
rollback_to_quick() {
  local ts; ts=$(date +%Y%m%d%H%M%S)
  mv "$CF_NAMED_CONF" "$CF_NAMED_CONF.failed-$ts" 2>/dev/null
  echo 0 > "$CF_FAIL_FILE" 2>/dev/null
  log "ROLLBACK -> quick tunnel (config kept as config.yml.failed-$ts)"
  for p in $(cf_pids); do kill -9 "$p" 2>/dev/null; done
  sleep 2
  CF_MODE=quick
  start_quick_tunnel
  CF_ACTION=named_failed_rollback
}

if [ ! -x "$CF_BIN" ]; then
  CF_ACTION=missing_binary
else
  running_mode=$(cf_running_mode)
  need=0
  if [ -z "$(cf_pids)" ]; then
    need=1; why=down
  elif [ "${running_mode:-unknown}" != "$CF_MODE" ]; then
    need=1; why="mode ${running_mode:-unknown}->$CF_MODE"
  fi

  if [ "$need" = "1" ]; then
    log "cloudflared $why"
    [ -n "$(cf_pids)" ] && { for p in $(cf_pids); do kill -9 "$p" 2>/dev/null; done; sleep 2; }
    if [ "$CF_MODE" = "named" ]; then
      if start_named_tunnel; then
        echo 0 > "$CF_FAIL_FILE" 2>/dev/null
      else
        fails=$(( $(cat "$CF_FAIL_FILE" 2>/dev/null || echo 0) + 1 ))
        echo "$fails" > "$CF_FAIL_FILE" 2>/dev/null
        log "named tunnel start failed (${fails}/${CF_NAMED_MAX_FAIL})"
        [ "$fails" -ge "$CF_NAMED_MAX_FAIL" ] && rollback_to_quick
      fi
    else
      start_quick_tunnel
    fi
  fi
fi

# —— 2.5 公网可达性自愈（2026-11-07 从 hub_tunnel_guard.sh 合并进来）——
# 为什么合并：两个脚本同时管隧道会互相拆台 —— 老的 hub_tunnel_guard 每轮只认「快速隧道」，
# 一旦切到固定域名，它会把命名隧道杀掉重建随机域名，固定域名 2~3 分钟就废了。
# 现在只留一个管隧道的：本地在跑 + 公网连续 2 轮不通 → 按**当前模式**重建
# （命名模式重建的还是命名隧道，绝不会因为固定域名不通就退回随机域名）。
# 注意：这里必须给出默认值。脚本开头是 set -u，裸引用未设置的可选变量会让整只脚本
# 直接退出（表现为「一失败就不干活、日志里刷 unbound variable」），而不是走重建逻辑。
# 这个坑真的踩过：线上 public_url 换成新域名前的那几分钟，守护每分钟崩一次，什么都不做。
HUB_CF_PUB_MAX_FAIL=${HUB_CF_PUB_MAX_FAIL:-2}
CF_PUB_FAIL_FILE="$RT/.cf_public_fails"
if [ "$CF_ACTION" = "none" ] && [ "$code" = "200" ] && [ -s "$CF_URL_FILE" ] && [ -n "$(cf_pids)" ]; then
  pub_url=$(tr -d ' \r\n' < "$CF_URL_FILE" 2>/dev/null)
  pcode=$(curl -s -m 20 -o /dev/null -w "%{http_code}" "$pub_url/api/health" 2>/dev/null)
  [ -z "$pcode" ] && pcode=000
  if [ "$pcode" = "200" ]; then
    echo 0 > "$CF_PUB_FAIL_FILE" 2>/dev/null
  else
    pf=$(tr -cd '0-9' < "$CF_PUB_FAIL_FILE" 2>/dev/null); pf=${pf:-0}
    pf=$((pf + 1))
    echo "$pf" > "$CF_PUB_FAIL_FILE" 2>/dev/null
    log "public health=$pcode ($pf/$HUB_CF_PUB_MAX_FAIL) url=$pub_url"
    if [ "$pf" -ge "$HUB_CF_PUB_MAX_FAIL" ]; then
      echo 0 > "$CF_PUB_FAIL_FILE" 2>/dev/null
      log "public unreachable -> rebuild tunnel (mode=$CF_MODE)"
      for p in $(cf_pids); do kill -9 "$p" 2>/dev/null; done
      sleep 2
      if [ "$CF_MODE" = "named" ]; then
        start_named_tunnel || rollback_to_quick
      else
        start_quick_tunnel
      fi
      CF_ACTION=rebuild_public
    fi
  fi
fi

if [ "$CF_MODE" = "named" ]; then
  # 域名写死在 config.yml 里，直接取；但只有「进程真的在 named 模式」时才能这么写，
  # 否则 public_url.txt 会写着固定域名、实际流量还在随机域名上（老版本的坑）。
  if [ "$(cf_running_mode)" = "named" ]; then
    # 别用 awk '{print $2}'：配置里是「  - hostname: zebra...」，$1 是短横线、$2 是字面量
    # hostname:，取出来会变成 https://hostname: 。这里直接抓 hostname: 后面的域名，不依赖行首格式。
    cf_host=$(grep -oE 'hostname:[[:space:]]*[A-Za-z0-9._-]+' "$CF_NAMED_CONF" 2>/dev/null \
      | head -1 | sed 's/.*hostname:[[:space:]]*//' | tr -d ' \r')
    [ -n "$cf_host" ] && echo "https://$cf_host" > "$CF_URL_FILE"
  else
    url=$(quick_url_latest)
    [ -n "$url" ] && echo "$url" > "$CF_URL_FILE"
  fi
else
  url=$(quick_url_latest)
  [ -n "$url" ] && echo "$url" > "$CF_URL_FILE"
fi

# —— 3. 状态快照（不刷日志，方便随时查看） ——
url_now=$(cat "$CF_URL_FILE" 2>/dev/null || echo "")
printf '{"ts":"%s","hub_health":"%s","hub_pids":"%s","hub_action":"%s","cf_mode":"%s","cf_action":"%s","public_url":"%s"}\n' \
  "$TS" "$code" "$(hub_pids | tr '\n' ',')" "$HUB_ACTION" "$CF_MODE" "$CF_ACTION" "$url_now" > "$RT/hub_guard_last.json"

exit 0
