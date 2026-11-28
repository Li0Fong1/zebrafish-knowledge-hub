#!/usr/bin/env bash
# =============================================================================
# zebrafish-hub.top 源站内核网络参数调优
#
# 交给服务器管理员用 root 执行一次即可：
#     sudo bash server_network_tuning.sh
#
# 背景（2026-11-12 实测）：
#   · 源站在国内，Cloudflare 隧道出口在洛杉矶，RTT 270 ms、丢包约 30%
#   · 路径 MTU 黑洞：payload 1400 丢 66%，1350 丢 33%，1200 丢 0%
#   · net.core.rmem_max 当前仅 212992 (208 KB)，cloudflared 启动日志明确报
#     「wanted 7168 KiB, got 416 KiB」—— QUIC 收包窗口被内核卡住
#   · tcp_mtu_probing 当前为 0（关闭），遇到 MTU 黑洞不会主动降包
#
# 这三条会让回源吞吐掉到 ~2 KB/s（实测 55 KB 要 24~40 s），
# 而同一份文件命中 CDN 只要 0.65 s。
#
# 本脚本**不动网卡 MTU**（br0 同时承载 SSH，改错会掉线），
# 只调整内核缓冲区与 TCP 路径 MTU 探测，可随时回滚。
# =============================================================================
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "请用 root 运行： sudo bash $0" >&2
    exit 1
fi

echo "=== 调整前 ==="
sysctl net.core.rmem_max net.core.wmem_max \
       net.core.rmem_default net.core.wmem_default \
       net.ipv4.tcp_mtu_probing

# 1) UDP 收发缓冲上限：给 QUIC 足够的窗口。
#    cloudflared 要 7 MB，这里给 16 MB 留余量；默认值 2 MB 让新 socket
#    不用显式 setsockopt 也能拿到合理缓冲。
sysctl -w net.core.rmem_max=16777216
sysctl -w net.core.wmem_max=16777216
sysctl -w net.core.rmem_default=2097152
sysctl -w net.core.wmem_default=2097152

# 2) TCP 路径 MTU 探测：静默丢包的链路上，让内核主动降 MSS，
#    而不是靠（往往被过滤掉的）ICMP Fragmentation Needed。
sysctl -w net.ipv4.tcp_mtu_probing=1

# 3) 固化到开机配置
cat > /etc/sysctl.d/99-zf-network.conf <<'EOF'
# zebrafish-hub.top 跨境链路调优 (2026-11-12)
net.core.rmem_max = 16777216
net.core.wmem_max = 16777216
net.core.rmem_default = 2097152
net.core.wmem_default = 2097152
net.ipv4.tcp_mtu_probing = 1
EOF
chmod 644 /etc/sysctl.d/99-zf-network.conf

echo
echo "=== 调整后 ==="
sysctl net.core.rmem_max net.core.wmem_max \
       net.core.rmem_default net.core.wmem_default \
       net.ipv4.tcp_mtu_probing

echo
echo "内核参数已生效并固化到 /etc/sysctl.d/99-zf-network.conf"
echo "注意：cloudflared 需要重启才能用上新的 UDP 缓冲上限。"
echo "     请通知站点维护者重启隧道（隧道以 deng 用户运行，不要用 root 启动）。"
echo
echo "回滚： sudo rm /etc/sysctl.d/99-zf-network.conf"
echo "       sudo sysctl -w net.core.rmem_max=212992 net.core.wmem_max=212992"
echo "       sudo sysctl -w net.core.rmem_default=212992 net.core.wmem_default=212992"
echo "       sudo sysctl -w net.ipv4.tcp_mtu_probing=0"
