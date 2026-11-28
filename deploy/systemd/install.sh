#!/bin/bash
# 安装 / 重装 / 卸载 Zebrafish Hub 的常驻守护（systemd --user，已开启 linger 可开机自启）
#
# 为什么不用 cron：这台机器的 `crontab` 对普通用户是禁止的（"You are not allowed to
# use this program"），root 的 cron 条目也早已失效 —— 结果就是 2026-10-27 之后
# watchdog 停了、hub_guard 也没人跑，全靠进程自己硬撑，出问题没人管。
#
# 用法（在项目根目录）：
#   bash deploy/systemd/install.sh            # 安装并立即启用
#   bash deploy/systemd/install.sh --status   # 只看状态
#   bash deploy/systemd/install.sh --remove   # 停用并删除单元
#
# 职责划分（避免两个脚本抢着重启同一个东西）：
#   zebrafish-hub-watchdog.service  → Hub：复活 / 冷启动预热 / 内存回收（systemd 常驻）
#   zebrafish-hub-guard.timer       → 隧道：模式对齐（快速 ⇄ 固定域名）+ 公网自愈
set -u

RT=/data/zoujin/deng/zebrafish_runtime
BASE=/data/zoujin/deng/zebrafish_gene_qa
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=$XDG_RUNTIME_DIR/bus}"

UNITS="zebrafish-hub-guard.service zebrafish-hub-guard.timer zebrafish-hub-watchdog.service"

case "${1:-install}" in
  --status)
    echo "=== linger（决定开机后服务是否自动起）==="
    loginctl show-user "$(id -un)" 2>/dev/null | grep -i linger || echo "Linger=no（需要 sudo loginctl enable-linger $(id -un)）"
    echo "=== timer ==="
    systemctl --user list-timers --no-pager 2>/dev/null | grep -E "zebrafish|NEXT" || echo "(无)"
    echo "=== service ==="
    systemctl --user is-active zebrafish-hub-guard.timer zebrafish-hub-watchdog.service 2>/dev/null | paste -sd' ' -
    echo "=== 最近一轮守护快照 ==="
    cat "$RT/hub_guard_last.json" 2>/dev/null || echo "(还没有)"
    echo
    echo "=== 看门狗快照 ==="
    cat "$RT/hub_watchdog_last.json" 2>/dev/null || echo "(还没有)"
    echo
    echo "=== 公网地址 ==="
    cat "$RT/public_url.txt" 2>/dev/null || echo "(未生成)"
    exit 0
    ;;
  --remove)
    for u in $UNITS; do systemctl --user disable --now "$u" 2>/dev/null; done
    systemctl --user daemon-reload 2>/dev/null
    for u in $UNITS; do rm -f "$UNIT_DIR/$u"; done
    systemctl --user daemon-reload 2>/dev/null
    echo "已停用并删除单元。注意：Hub 与隧道进程不会被停掉，继续运行。"
    exit 0
    ;;
esac

set -e
mkdir -p "$UNIT_DIR"
for u in $UNITS; do
  install -m 644 "$SRC_DIR/$u" "$UNIT_DIR/$u"
  echo "已安装 $UNIT_DIR/$u"
done
systemctl --user daemon-reload
systemctl --user enable --now zebrafish-hub-guard.timer
systemctl --user enable --now zebrafish-hub-watchdog.service
sleep 2

echo
echo "=== 校验 ==="
systemctl --user is-enabled zebrafish-hub-guard.timer zebrafish-hub-watchdog.service 2>/dev/null | paste -sd' ' -
systemctl --user is-active  zebrafish-hub-guard.timer zebrafish-hub-watchdog.service 2>/dev/null | paste -sd' ' -
echo
systemctl --user list-timers --no-pager 2>/dev/null | head -3
echo
echo "立刻跑一轮守护（观察它有没有乱动正在跑的隧道）："
systemctl --user start zebrafish-hub-guard.service || true
sleep 3
cat "$RT/hub_guard_last.json" 2>/dev/null || true
echo
echo "提示：看日志  journalctl --user -u zebrafish-hub-guard.service -n 30"
echo "     或直接  tail -20 $RT/logs/hub_guard.log"
