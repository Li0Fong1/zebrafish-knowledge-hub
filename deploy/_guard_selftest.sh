#!/bin/bash
# hub_guard.sh 的隔离桩测试（在服务器上跑，不碰生产）
#
# 为什么要有它：隧道模式切换 / 回退是「一按就可能让站点失联」的操作，域名买回来后只能成功
# 不能试错。这里用一个假的 cloudflared_run（只写日志、不连网）把四种情况跑一遍：
#   case1 冷启动         无配置、无进程        → 起快速隧道，public_url 是随机域名
#   case2 切到固定域名    放入可用 config.yml  → 杀掉快速隧道、起命名隧道，public_url 变固定域名
#   case3 回退            删掉 config.yml      → 杀掉命名隧道、起快速隧道，public_url 回到随机域名
#   case4 命名隧道起不来  放入坏 config.yml    → 连续失败 2 次后自动回退，坏配置改名留档
#
# 用 HUB_RT/HUB_BASE 把守护指到 /tmp 下的临时目录；因为 cf_pids 按 $CF_BIN 绝对路径匹配，
# 测试进程与生产隧道（$RT/bin/cf_stub）互不干扰。
set -u
GUARD=${1:?用法: _guard_selftest.sh <hub_guard.sh 路径>}
T=/tmp/zfguard_test
RT=$T/rt
BASE=$T/base

pass=0; fail=0
ok()   { echo "  ✅ $*"; pass=$((pass+1)); }
bad()  { echo "  ❌ $*"; fail=$((fail+1)); }

stop_all() { for p in $(ps -eo pid,args | grep -F "$RT/bin/cf_stub" | grep -v grep | awk '{print $1}'); do kill -9 "$p" 2>/dev/null; done; sleep 1; }

setup() {
  stop_all
  rm -rf "$T"; mkdir -p "$RT/bin" "$RT/logs" "$RT/cloudflared" "$BASE"
  : > "$RT/logs/cloudflared_hub.log"
  cat > "$RT/bin/cf_stub" <<'STUB'
#!/bin/bash
# 假 cloudflared：按参数决定「注册成功 / 失败」，然后常驻（不连网）
log="${HUB_STUB_LOG:-/tmp/zfguard_test/rt/logs/cloudflared_hub.log}"
case " $* " in
  *" --config "*)
    conf=""
    prev=""
    for a in "$@"; do [ "$prev" = "--config" ] && conf=$a; prev=$a; done
    if grep -q FAIL_ME "$conf" 2>/dev/null; then
      echo "$(date '+%F %T') ERR failed to connect to origin / tunnel credentials invalid" >> "$log"
      exit 1
    fi
    echo "$(date '+%F %T') INF Registered tunnel connection connIndex=0" >> "$log"
    ;;
  *)
    echo "$(date '+%F %T') INF |  https://guard-selftest-$(date +%s).trycloudflare.com  |" >> "$log"
    ;;
esac
# 不能写 exec sleep：exec 会把命令行换成 "sleep 3600"，守护按 $CF_BIN 路径匹配就找不到它了，
# 于是每轮都判「隧道没起」并反复重启。原地循环才能保住原始命令行。
while :; do sleep 60; done
STUB
  chmod 755 "$RT/bin/cf_stub"
}

# 每个 case 都从「零失败计数」开始，否则上一 case 的失败会累积到下一 case，
# 让 case4 的「第 1 次不该回退」变成假失败。
reset_fails() { rm -f "$RT/cloudflared/.cf_named_fails" 2>/dev/null; }

# 桩进程不会真的对外提供服务，所以默认把「公网自愈」阈值调到很高，避免每轮都判不通去重建；
# case5/case6 需要它生效时用 run_guard_pub。
run_guard() {
  HUB_RT=$RT HUB_BASE=$BASE HUB_GUARD_MEM_RECYCLE=0 HUB_CF_NAMED_MAX_FAIL=2 \
  HUB_CF_PUB_MAX_FAIL=999 HUB_CF_BIN=$RT/bin/cf_stub \
    bash "$GUARD" >> "$T/guard.out" 2>&1
}
# 故意不设 HUB_CF_PUB_MAX_FAIL：线上 systemd 单元就是这么跑的。
# 脚本是 set -u，任何裸引用未设置的可选变量都会让守护直接退出 —— 这个 bug 真上过线。
run_guard_bare() {
  env -u HUB_CF_PUB_MAX_FAIL \
    HUB_RT=$RT HUB_BASE=$BASE HUB_GUARD_MEM_RECYCLE=0 HUB_CF_NAMED_MAX_FAIL=2 \
    HUB_CF_BIN=$RT/bin/cf_stub \
    bash "$GUARD" >> "$T/guard.out" 2>&1
}
run_guard_pub() {
  HUB_RT=$RT HUB_BASE=$BASE HUB_GUARD_MEM_RECYCLE=0 HUB_CF_NAMED_MAX_FAIL=2 \
  HUB_CF_PUB_MAX_FAIL=2 HUB_CF_BIN=$RT/bin/cf_stub \
    bash "$GUARD" >> "$T/guard.out" 2>&1
}

mode_now() {
  local l
  l=$(ps -eo args | grep -F "$RT/bin/cf_stub" | grep -v grep | grep -F " tunnel" | head -1)
  [ -z "$l" ] && { echo none; return; }
  case "$l" in *--config*) echo named ;; *) echo quick ;; esac
}

echo "=============================================================="
echo "hub_guard.sh 桩测试   被测文件：$GUARD"
echo "=============================================================="

# ---------------- case1 ----------------
echo "[case1] 冷启动（无配置 / 无进程）→ 快速隧道"
setup
run_guard
[ "$(mode_now)" = "quick" ] && ok "进程跑在 quick" || bad "进程模式=$(mode_now)，应为 quick"
grep -q "trycloudflare.com" "$RT/public_url.txt" 2>/dev/null && ok "public_url 记的是随机域名" \
  || bad "public_url=$(cat "$RT/public_url.txt" 2>/dev/null)"

# ---------------- case2 ----------------
reset_fails
echo "[case2] 放入可用 config.yml → 切命名隧道（老版本这里不会切）"
cat > "$RT/cloudflared/config.yml" <<EOF
tunnel: zebrafish-hub
credentials-file: $RT/cloudflared/x.json
ingress:
  - hostname: zebrafish-hub.top
    service: http://127.0.0.1:8080
EOF
echo zebrafish-hub > "$RT/cloudflared/tunnel_name.txt"
run_guard
[ "$(mode_now)" = "named" ] && ok "进程已切到 named" || bad "进程模式=$(mode_now)，应为 named"
[ "$(cat "$RT/public_url.txt" 2>/dev/null)" = "https://zebrafish-hub.top" ] && ok "public_url=https://zebrafish-hub.top" \
  || bad "public_url=$(cat "$RT/public_url.txt" 2>/dev/null)"
n=$(ps -eo args | grep -F "$RT/bin/cf_stub" | grep -v grep | grep -cF " tunnel")
[ "$n" = "1" ] && ok "没有残留旧进程" || bad "进程数=$n，应为 1"

# ---------------- case3 ----------------
reset_fails
echo "[case3] 删掉 config.yml → 回退快速隧道（--revert 走的就是这条）"
rm -f "$RT/cloudflared/config.yml"
run_guard
[ "$(mode_now)" = "quick" ] && ok "进程已退回 quick" || bad "进程模式=$(mode_now)，应为 quick"
grep -q "trycloudflare.com" "$RT/public_url.txt" 2>/dev/null && ok "public_url 回到随机域名" \
  || bad "public_url=$(cat "$RT/public_url.txt" 2>/dev/null)"

# ---------------- case4 ----------------
reset_fails
echo "[case4] 命名隧道起不来 → 连续失败 2 次自动回退，坏配置改名留档"
printf 'tunnel: zebrafish-hub\ncredentials-file: %s/FAIL_ME.json\n' "$RT/cloudflared" > "$RT/cloudflared/config.yml"
run_guard                       # 第 1 次：失败，计数 1，暂不回退
[ -f "$RT/cloudflared/config.yml" ] && ok "第 1 次失败未回退（保留重试机会）" || bad "第 1 次就回退了，过于激进"
run_guard                       # 第 2 次：失败，计数 2 → 回退
ls "$RT"/cloudflared/config.yml.failed-* >/dev/null 2>&1 && ok "坏配置已改名留档 .failed-*" \
  || bad "没有 .failed-* 留档"
[ ! -f "$RT/cloudflared/config.yml" ] && ok "config.yml 已移走，下一轮不会再试命名隧道" || bad "config.yml 还在"
[ "$(mode_now)" = "quick" ] && ok "已自动回退到 quick，站点没有失联" || bad "进程模式=$(mode_now)，应为 quick"
grep -q "trycloudflare.com" "$RT/public_url.txt" 2>/dev/null && ok "public_url 回到随机域名" \
  || bad "public_url=$(cat "$RT/public_url.txt" 2>/dev/null)"

# ---------------- case5 ----------------
reset_fails
echo "[case5] 快速模式 + 公网不通 → 重建后仍是 quick"
echo "http://127.0.0.1:9" > "$RT/public_url.txt"
before_pid=$(ps -eo pid,args | grep -F "$RT/bin/cf_stub" | grep -v grep | awk '{print $1}' | head -1)
run_guard_pub
run_guard_pub
after_pid=$(ps -eo pid,args | grep -F "$RT/bin/cf_stub" | grep -v grep | awk '{print $1}' | head -1)
[ -n "$after_pid" ] && [ "$after_pid" != "$before_pid" ] && ok "隧道已重建（pid $before_pid → $after_pid）" \
  || bad "隧道未重建（pid $before_pid → $after_pid）"
[ "$(mode_now)" = "quick" ] && ok "重建后仍是 quick" || bad "重建后模式=$(mode_now)"

# ---------------- case6 ----------------
reset_fails
echo "[case6] 命名模式 + 公网不通 → 重建的必须还是命名隧道（不能退回随机域名）"
cat > "$RT/cloudflared/config.yml" <<EOF
tunnel: zebrafish-hub
credentials-file: $RT/cloudflared/x.json
ingress:
  - hostname: zebrafish-hub.top
    service: http://127.0.0.1:8080
EOF
echo "http://127.0.0.1:9" > "$RT/public_url.txt"
run_guard
[ "$(mode_now)" = "named" ] && ok "先切到 named" || bad "未切到 named，模式=$(mode_now)"
run_guard_pub
run_guard_pub
[ "$(mode_now)" = "named" ] && ok "公网连续失败后重建的仍是 named" || bad "被退回 $(mode_now)，固定域名会失效"
grep -q "zebrafish-hub" "$RT/public_url.txt" 2>/dev/null && ok "public_url 仍是固定域名" \
  || bad "public_url=$(cat "$RT/public_url.txt" 2>/dev/null)"

# ---------------- case7 ----------------
reset_fails
echo "[case7] 不设置 HUB_CF_PUB_MAX_FAIL（线上真实情形）→ 守护不能崩"
rm -f "$RT/cloudflared/config.yml"
run_guard_bare
[ "$(mode_now)" = "quick" ] && ok "先在 quick 模式" || bad "模式=$(mode_now)"
echo "http://127.0.0.1:9" > "$RT/public_url.txt"
before_pid=$(ps -eo pid,args | grep -F "$RT/bin/cf_stub" | grep -v grep | awk '{print $1}' | head -1)
run_guard_bare
run_guard_bare
after_pid=$(ps -eo pid,args | grep -F "$RT/bin/cf_stub" | grep -v grep | awk '{print $1}' | head -1)
grep -q "unbound variable" "$T/guard.out" && bad "日志出现 unbound variable（set -u 崩了）" \
  || ok "无 unbound variable"
[ -n "$after_pid" ] && [ "$after_pid" != "$before_pid" ] && ok "公网连续失败后仍能重建隧道（pid $before_pid → $after_pid）" \
  || bad "未重建（pid $before_pid → $after_pid）"

# ---------------- 收尾 ----------------
stop_all
echo "--------------------------------------------------------------"
echo "结果：通过 $pass 项，失败 $fail 项"
echo "日志：$T/guard.out"
echo "--------------------------------------------------------------"
[ "$fail" = "0" ] || exit 1
exit 0
