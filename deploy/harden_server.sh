#!/bin/bash
# ==============================================================================
# zebrafish-hub 服务器安全加固（需 root / sudo）
#
#   审计模式（默认，只读，不改任何东西）：
#       sudo bash deploy/harden_server.sh
#
#   执行加固：
#       sudo bash deploy/harden_server.sh --apply
#
# 设计原则：
#   · 幂等：重复执行不会叠加规则、不会重复装包；
#   · 不锁死自己：开启 ufw 之前先把 SSH 端口放行，且端口从 sshd 配置里读，
#     不写死 22；
#   · 不动密码登录：本机目前靠密码 + 非标准端口登录，贸然关掉会把运维自己
#     关在门外。要改成密钥登录请看脚本末尾的说明，确认密钥可用后再关。
# ==============================================================================
set -uo pipefail

APPLY=0
[ "${1:-}" = "--apply" ] && APPLY=1

ok()   { printf '  [OK]   %s\n' "$*"; }
warn() { printf '  [!!]   %s\n' "$*"; }
info() { printf '  [--]   %s\n' "$*"; }
head_() { printf '\n=== %s ===\n' "$*"; }

[ "$(id -u)" -ne 0 ] && { echo "请用 sudo 运行：sudo bash $0 $*"; exit 1; }

SSH_PORT=$(sshd -T 2>/dev/null | awk '/^port /{print $2; exit}')
SSH_PORT=${SSH_PORT:-$(grep -Ei '^[[:space:]]*Port[[:space:]]' /etc/ssh/sshd_config 2>/dev/null | awk '{print $2; exit}')}
SSH_PORT=${SSH_PORT:-22}

# ------------------------------------------------------------------ 审计
head_ "1. 对外监听端口"
ss -ltnp 2>/dev/null | awk 'NR==1 || $4 !~ /^127\.|^\[::1\]/' | sed 's/^/  /'
PUBLIC_LISTEN=$(ss -ltn 2>/dev/null | awk '$4 ~ /^0\.0\.0\.0:/ || $4 ~ /^\[::\]:/ {print $4}' | sort -u)
if [ -n "$PUBLIC_LISTEN" ]; then
    warn "以下端口监听在所有网卡上（本机无公网 IP，仅内网可达，但同网段可访问）："
    echo "$PUBLIC_LISTEN" | sed 's/^/         /'
    info "处理建议：只给本机用的服务改成 127.0.0.1 绑定，或在 ufw 里 deny 掉。"
else
    ok "没有监听在 0.0.0.0 的端口"
fi

head_ "2. SSH 配置（生效值）"
for k in port permitrootlogin passwordauthentication pubkeyauthentication maxauthtries; do
    v=$(sshd -T 2>/dev/null | awk -v k="$k" '$1==k{print $2; exit}')
    printf '  %-24s %s\n' "$k" "${v:-未知}"
done
ROOT_LOGIN=$(sshd -T 2>/dev/null | awk '/^permitrootlogin/{print $2; exit}')
[ "${ROOT_LOGIN:-no}" = "yes" ] && warn "允许 root 直接登录，建议改 no" || ok "禁止 root 直接登录"

head_ "3. 防火墙"
if command -v ufw >/dev/null 2>&1; then
    ufw status verbose 2>/dev/null | sed 's/^/  /'
else
    warn "未安装 ufw"
fi

head_ "4. 暴力破解防护"
if command -v fail2ban-client >/dev/null 2>&1 && fail2ban-client status >/dev/null 2>&1; then
    fail2ban-client status 2>/dev/null | sed 's/^/  /'
    ok "fail2ban 运行中"
else
    warn "fail2ban 未运行 —— SSH 暴露在公网时这是最需要补的一项"
fi

head_ "5. 最近 24 小时 SSH 失败登录"
FAILED=$(journalctl -u ssh --since "24 hours ago" --no-pager 2>/dev/null | grep -c "Failed password" || true)
info "失败次数：${FAILED:-0}"
if [ "${FAILED:-0}" -gt 200 ]; then
    warn "失败次数偏高，基本可以确定在被扫描/爆破，务必启用 fail2ban"
fi

head_ "6. 待安装的安全更新"
if command -v apt-get >/dev/null 2>&1; then
    apt-get -s upgrade 2>/dev/null | grep -c '^Inst.*security' | sed 's/^/  security 包数：/'
fi

if [ "$APPLY" -ne 1 ]; then
    printf '\n审计完成。要真正加固，执行：sudo bash %s --apply\n' "$0"
    exit 0
fi

# ------------------------------------------------------------------ 加固
head_ "7. 安装并启用 fail2ban"
if ! command -v fail2ban-client >/dev/null 2>&1; then
    DEBIAN_FRONTEND=noninteractive apt-get update -qq && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq fail2ban || { warn "fail2ban 安装失败"; }
fi
if [ -d /etc/fail2ban ]; then
    cat > /etc/fail2ban/jail.local <<EOF
[DEFAULT]
bantime  = 1h
findtime = 10m
maxretry = 5
# 本机与内网不封，避免运维自己把自己关掉
ignoreip = 127.0.0.1/8 ::1 192.168.0.0/24 192.168.122.0/24

[sshd]
enabled = true
port    = $SSH_PORT
EOF
    systemctl enable fail2ban >/dev/null 2>&1
    systemctl restart fail2ban
    sleep 2
    fail2ban-client status sshd 2>/dev/null | sed 's/^/  /' || warn "fail2ban 启动异常，请查看 journalctl -u fail2ban"
    ok "fail2ban 已配置（sshd 端口 $SSH_PORT，5 次/10 分钟 封 1 小时）"
fi

head_ "8. 防火墙默认策略"
if command -v ufw >/dev/null 2>&1; then
    # 顺序很重要：先放行 SSH，再启用，否则会把自己踢下线
    ufw allow "$SSH_PORT"/tcp comment 'ssh' >/dev/null 2>&1
    ufw default deny incoming >/dev/null 2>&1
    ufw default allow outgoing >/dev/null 2>&1
    if ! ufw status | grep -q "Status: active"; then
        echo "y" | ufw enable >/dev/null 2>&1
    fi
    ufw reload >/dev/null 2>&1
    ok "ufw 默认拒绝入站，已放行 $SSH_PORT/tcp"
    ufw status numbered 2>/dev/null | sed 's/^/  /'
else
    warn "跳过（未安装 ufw）"
fi

head_ "9. SSH 基础加固"
SSHD_BAK="/etc/ssh/sshd_config.bak_$(date +%Y%m%d_%H%M%S)"
cp /etc/ssh/sshd_config "$SSHD_BAK"
set_opt() {
    local k=$1 v=$2
    if grep -qiE "^[[:space:]]*#?[[:space:]]*$k[[:space:]]+" /etc/ssh/sshd_config; then
        sed -i -E "s|^[[:space:]]*#?[[:space:]]*$k[[:space:]]+.*|$k $v|I" /etc/ssh/sshd_config
    else
        echo "$k $v" >> /etc/ssh/sshd_config
    fi
}
set_opt PermitRootLogin no
set_opt MaxAuthTries 3
set_opt LoginGraceTime 30
set_opt ClientAliveInterval 300
set_opt ClientAliveCountMax 2
if sshd -t 2>/dev/null; then
    systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null
    ok "sshd 配置已加固并重载（备份：$SSHD_BAK）"
else
    cp "$SSHD_BAK" /etc/ssh/sshd_config
    warn "新配置校验失败，已回滚，未重载"
fi

head_ "10. 自动安全更新"
if command -v dpkg-reconfigure >/dev/null 2>&1; then
    cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF
    ok "已开启每日自动更新源 + 自动装安全补丁"
fi

printf '\n加固完成。请另开一个终端确认还能 SSH 登录后再关闭当前会话。\n'
cat <<'NOTE'

如需进一步改成「密钥登录、彻底关闭密码」：
  1) 本机生成密钥：ssh-keygen -t ed25519
  2) 上传公钥：ssh-copy-id -p 43333 deng@<服务器>
  3) 新开终端用密钥验证能登录
  4) 确认无误后：sudo sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
                 sudo systemctl reload ssh
  注意：第 3 步没验证通过就执行第 4 步，会永久失去远程登录能力。
NOTE
