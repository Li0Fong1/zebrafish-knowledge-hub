# -*- coding: utf-8 -*-
"""给斑马鱼 Hub 配一个**固定域名**（Cloudflare 命名隧道）。

为什么需要它
------------
现在公网走的是 cloudflared **快速隧道**，域名由 Cloudflare 随机发、隧道一重启就换，
文档里已经记着好几个失效域名了。要做到「固定 + 安全 + 稳定 + 自己命名」，
只能换成**命名隧道**：域名绑定在你自己 Cloudflare 账号下的一个域名上，永久不变。

本脚本帮你把除「买域名 + 浏览器授权」之外的活全干完：
    登录 → 建隧道 → 绑域名 → 渲染 config.yml → 上传到服务器 → 起隧道 → 公网实测

用法（在项目根目录跑）
----------------------
    .venv\\Scripts\\python.exe -X utf8 scripts\\setup_named_tunnel.py                       # 交互式全流程
    .venv\\Scripts\\python.exe -X utf8 scripts\\setup_named_tunnel.py --hostname hub.x.com  # 免交互
    .venv\\Scripts\\python.exe -X utf8 scripts\\setup_named_tunnel.py --status              # 只看当前是什么模式/什么域名
    .venv\\Scripts\\python.exe -X utf8 scripts\\setup_named_tunnel.py --revert              # 退回快速隧道（随机域名）

前提
----
1. 你有一个域名，且已把 **NS 改到 Cloudflare**（免费版即可，几条 NS 记录，等生效）；
2. 该域名已加进你的 Cloudflare 账号（Zone 状态 Active）；
3. 本机首次跑要能开浏览器完成一次授权（此后不再需要）。

安全说明
--------
· 命名隧道是**服务器主动往外连**，不需要服务器 root、不开任何入站端口、不改防火墙；
· 只有隧道凭据 json 会放到服务器上（<RT>/cloudflared/），它只能用来跑这一条隧道；
· 服务器上与你无关的进程一律不动（只匹配我们自己那个叫 cloudflared_run 的进程）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import paramiko  # noqa: E402

# ---------------- 服务器常量（与 hub_guard.sh 保持一致） ----------------
try:  # 凭据只从 .env / 环境变量读，源码内不得硬编码（见 RELEASE_SECURITY_AUDIT）
    from _zf_creds import HOST, PORT, USER, require_password
except ImportError:  # 以包形式导入时（python -m scripts.xxx）
    from scripts._zf_creds import HOST, PORT, USER, require_password
RT = "/data/zoujin/deng/zebrafish_runtime"
CF_DIR = RT + "/cloudflared"                 # 配置与凭据
CF_BIN_DIR = RT + "/bin"                     # cloudflared 二进制（持久目录）
CF_BIN = CF_BIN_DIR + "/cloudflared_run"
CF_CONF = CF_DIR + "/config.yml"
CF_NAME_FILE = CF_DIR + "/tunnel_name.txt"
CF_LOG = RT + "/logs/cloudflared_hub.log"
TUNNEL_NAME = "zebrafish-hub"

# ---------------- 本地工具 ----------------
TOOLS = ROOT / "tools"
CF_EXE = TOOLS / "cloudflared.exe"
CF_LINUX = TOOLS / "cloudflared-linux-amd64"
CF_WIN_URL = ("https://github.com/cloudflare/cloudflared/releases/latest/download/"
              "cloudflared-windows-amd64.exe")
CF_LINUX_URL = ("https://github.com/cloudflare/cloudflared/releases/latest/download/"
                "cloudflared-linux-amd64")
CF_HOME = Path(os.environ.get("USERPROFILE") or Path.home()) / ".cloudflared"

UUID_RE = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", re.I)
HOSTNAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$", re.I)


def say(msg: str = "") -> None:
    print(msg, flush=True)


def step(n, total, title: str) -> None:
    say("\n" + "=" * 78)
    say("【%d/%d】%s" % (n, total, title))
    say("=" * 78)


def die(msg: str, code: int = 1):
    say("\n❌ " + msg)
    sys.exit(code)


# --------------------------------------------------------------------- 本地工具
def ensure_exe(path: Path, url: str, what: str) -> Path:
    """缺了就从官方 release 下（GitHub 直链）。"""
    if path.is_file() and path.stat().st_size > 1_000_000:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    say("   下载 %s …（约 20MB，首次才会下）" % what)
    tmp = str(path) + ".download"
    urllib.request.urlretrieve(url, tmp)
    shutil.move(tmp, path)
    return path


def cf(args, capture: bool = True, timeout: int = 300):
    """跑本机 cloudflared。capture=False 时把输出直接给用户看（登录要用）。"""
    cmd = [str(CF_EXE)] + args
    if capture:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    return subprocess.call(cmd), ""


def cf_login_ok() -> bool:
    return (CF_HOME / "cert.pem").is_file()


def ensure_login() -> None:
    if cf_login_ok():
        say("   已授权（找到 %s）" % (CF_HOME / "cert.pem"))
        return
    say("   本机还没授权过。接下来会打开浏览器，请登录 Cloudflare 并**选中你的域名**后点 Authorize。")
    say("   （只做这一次；窗口里出现 https://dash.cloudflare.com/... 时按提示完成即可）")
    input("   按回车开始…")
    rc, _ = cf(["tunnel", "login"], capture=False, timeout=1800)
    if rc != 0 or not cf_login_ok():
        die("授权失败或未完成。请重试；若浏览器没弹出，可手动打开命令里打印的链接。")
    say("   ✅ 授权完成")


def tunnel_uuid_of(name: str) -> str | None:
    """从 `tunnel list` 里找同名隧道的 UUID。"""
    rc, out = cf(["tunnel", "list"])
    if rc != 0:
        return None
    for line in out.splitlines():
        if name in line:
            m = UUID_RE.search(line)
            if m:
                return m.group(1).lower()
    return None


def ensure_tunnel(name: str) -> str:
    """建隧道；已存在就复用。返回 UUID。"""
    rc, out = cf(["tunnel", "create", name])
    if rc == 0:
        m = UUID_RE.search(out)
        if m:
            say("   ✅ 已创建隧道 %s（UUID %s）" % (name, m.group(1)))
            return m.group(1).lower()
    # 名字已被占用：报错里通常就带 UUID（“…already exists with id <uuid>”），先直接抓
    m = UUID_RE.search(out)
    if m:
        say("   ✅ 隧道 %s 已存在，复用（UUID %s）" % (name, m.group(1)))
        return m.group(1).lower()
    uid = tunnel_uuid_of(name)
    if uid:
        say("   ✅ 隧道 %s 已存在，复用（UUID %s）" % (name, uid))
        return uid
    die("创建隧道失败：\n" + out.strip())
    raise SystemExit(1)


def ensure_dns(name: str, hostname: str) -> None:
    rc, out = cf(["tunnel", "route", "dns", name, hostname])
    if rc == 0:
        say("   ✅ 已把 %s 指向隧道 %s" % (hostname, name))
        return
    low = out.lower()
    if "already exists" in low or "record with that host already exists" in low:
        say("   ✅ DNS 记录已存在（%s），跳过" % hostname)
        return
    say(out.strip())
    die("绑定域名失败。常见原因：该域名不在你已授权的 Cloudflare 账号下、或 NS 还没生效。")


def credentials_path(uid: str) -> Path:
    return CF_HOME / ("%s.json" % uid)


def render_config(uid: str) -> str:
    tpl = ROOT / "deploy" / "cloudflared" / "config.yml.template"
    text = tpl.read_text(encoding="utf-8")
    return text.replace("REPLACE_WITH_TUNNEL_UUID", uid)


# --------------------------------------------------------------------- 服务器
class Remote:
    def __init__(self) -> None:
        pwd = require_password()
        self.cli = paramiko.SSHClient()
        self.cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.cli.connect(HOST, port=PORT, username=USER, password=pwd, timeout=45,
                         banner_timeout=60, auth_timeout=60,
                         allow_agent=False, look_for_keys=False)
        self.cli.get_transport().set_keepalive(15)

    def sh(self, cmd: str, timeout: int = 120) -> str:
        ch = self.cli.get_transport().open_session()
        ch.settimeout(timeout)
        ch.exec_command(cmd)
        out = b""
        while True:
            try:
                buf = ch.recv(65536)
            except Exception:
                break
            if not buf:
                break
            out += buf
        ch.close()
        return out.decode("utf-8", "replace")

    def put(self, local: Path, remote: str) -> None:
        sftp = self.cli.open_sftp()
        try:
            sftp.put(str(local), remote)
        finally:
            sftp.close()

    def put_text(self, text: str, remote: str) -> None:
        sftp = self.cli.open_sftp()
        try:
            with sftp.open(remote, "w") as f:
                f.write(text)
        finally:
            sftp.close()

    def close(self) -> None:
        self.cli.close()


def push_to_server(r: Remote, uid: str, hostname: str) -> None:
    r.sh("mkdir -p %s %s %s" % (CF_DIR, CF_BIN_DIR, RT + "/logs"))

    # 1) 二进制：换成持久目录，避免服务器重启后 /tmp 被清掉
    txt = r.sh("test -x %s && stat -c %%s %s || echo 0" % (CF_BIN, CF_BIN)).strip()
    size = int(txt) if txt.isdigit() else 0
    if size > 1_000_000:
        say("   二进制已就位（%s，%.1f MB）" % (CF_BIN, size / 1048576))
    else:
        ensure_exe(CF_LINUX, CF_LINUX_URL, "cloudflared（Linux 版）")
        say("   上传二进制 → %s" % CF_BIN)
        r.put(CF_LINUX, CF_BIN)
        r.sh("chmod 755 %s" % CF_BIN)

    # 2) 凭据（只上传这一个 json，cert.pem 留在本机）
    cred = credentials_path(uid)
    if not cred.is_file():
        die("找不到凭据文件 %s —— 隧道是不是建在别的账号下？" % cred)
    say("   上传凭据 → %s/%s.json" % (CF_DIR, uid))
    r.put(cred, "%s/%s.json" % (CF_DIR, uid))
    r.sh("chmod 600 %s/%s.json" % (CF_DIR, uid))

    # 3) config.yml（把 UUID 与凭据的**服务器绝对路径**填好）
    text = render_config(uid)
    text = text.replace("REPLACE_WITH_CREDENTIALS_JSON_PATH", "%s/%s.json" % (CF_DIR, uid))
    text = text.replace("REPLACE_WITH_HOSTNAME", hostname)
    if "REPLACE_WITH" in text:
        die("配置模板里还有没替换掉的占位符，请检查 deploy/cloudflared/config.yml.template")
    say("   上传配置 → %s" % CF_CONF)
    backup = r.sh("if [ -f %s ]; then cp -p %s %s.bak-$(date +%%Y%%m%%d%%H%%M%%S) "
                  "&& echo saved; else echo none; fi" % (CF_CONF, CF_CONF, CF_CONF)).strip()
    say("   旧配置：%s" % ("已备份为 .bak-<时间戳>" if backup == "saved" else "无（首次配置）"))
    r.put_text(text, CF_CONF)
    r.put_text(TUNNEL_NAME, CF_NAME_FILE)


def restart_tunnel(r: Remote) -> None:
    """停掉快速隧道进程，按命名隧道重新拉起（只动我们自己那个 cloudflared_run）。

    注意 `cloudflared_run[ ]tunnel` 这种字符类写法：pkill/pgrep 的 -f 是匹配整条命令行，
    而这条命令自身的 cmdline 里就含关键字 —— 直写会被自己匹配到，pkill 会把自己的会话杀掉。
    """
    r.sh("pkill -f 'cloudflared_run[ ]tunnel' ; sleep 2; echo done")
    name = TUNNEL_NAME
    r.sh("cd %s && setsid nohup %s tunnel --no-autoupdate --config %s run %s "
         ">> %s 2>&1 </dev/null & sleep 3; echo started" % (RT, CF_BIN, CF_CONF, name, CF_LOG))
    say("   已拉起命名隧道（%s）" % name)


def verify_public(r: Remote, hostname: str, tries: int = 12) -> bool:
    url = "https://%s/api/health" % hostname
    for i in range(1, tries + 1):
        code = r.sh("curl -s -m 15 -o /dev/null -w '%%{http_code}' %s" % url).strip()
        say("   第 %2d 次探测 %s → HTTP %s" % (i, url, code))
        if code == "200":
            return True
        if code in ("502", "530"):
            # 隧道通了但源站没起来：本机 8080 有没有服务？
            local = r.sh("curl -s -m 10 -o /dev/null -w '%%{http_code}' "
                         "http://127.0.0.1:8080/api/health").strip()
            if local != "200":
                say("   ⚠️ 源站未就绪（本机 8080 = HTTP %s）。"
                    "服务应该是被停过，先恢复再验证。" % local)
                return False
        time.sleep(10)
    return False


def show_status(r: Remote) -> None:
    say(r.sh("echo '=== 守护快照 ==='; cat %s/hub_guard_last.json; echo; "
             "echo '=== 模式判定 ==='; "
             "if [ -f %s ]; then echo 'named（命名隧道，固定域名）'; "
             "awk '/hostname:/{print \"  hostname: \"$2; exit}' %s; "
             "else echo 'quick（快速隧道，随机域名，重启就换）'; fi; "
             "echo '=== 隧道进程 ==='; "
             "n=$(pgrep -cf 'cloudflared_run[ ]tunnel' || true); "
             "if [ \"${n:-0}\" = \"0\" ]; then echo '(无 —— 隧道当前未运行)'; "
             "else pgrep -af 'cloudflared_run[ ]tunnel' | cut -c1-150; fi; "
             "echo '=== 二进制位置 ==='; ls -l %s 2>/dev/null || echo '(持久目录里没有)'; "
             "ls -l /tmp/cloudflared_run 2>/dev/null || true"
             % (RT, CF_CONF, CF_CONF, CF_BIN), 90))


def do_revert(r: Remote) -> None:
    say("   删除 %s（守护下一分钟自动退回快速隧道）" % CF_CONF)
    r.sh("mkdir -p %s && if [ -f %s ]; then mv %s %s.bak-$(date +%%Y%%m%%d%%H%%M%%S); fi; echo ok"
         % (CF_DIR, CF_CONF, CF_CONF, CF_CONF))
    say("   已退回。接下来隧道会用随机 trycloudflare 域名，地址见 %s/public_url.txt" % RT)


# --------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(description="给斑马鱼 Hub 配固定域名（Cloudflare 命名隧道）")
    ap.add_argument("--hostname", help="你要用的固定域名，如 hub.example.com")
    ap.add_argument("--status", action="store_true", help="只看当前模式与域名")
    ap.add_argument("--revert", action="store_true", help="退回快速隧道（随机域名）")
    args = ap.parse_args()

    r = Remote()
    try:
        if args.status:
            step(1, 1, "当前公网模式")
            show_status(r)
            return 0
        if args.revert:
            step(1, 1, "退回快速隧道")
            do_revert(r)
            return 0

        total = 6
        hostname = (args.hostname or "").strip().lower()
        if not hostname:
            say("先确认一件事：**你必须有自己名下的域名**（服务器也不是你名下的，")
            say("所以没法借用人家的名字）。域名要已加到 Cloudflare 账号且状态 Active。")
            say("例：hub.yourname.com")
            hostname = input("请输入你要用的固定域名：").strip().lower()
        if not HOSTNAME_RE.match(hostname) or hostname.count(".") < 2:
            die("域名格式不对：应形如 hub.yourname.com（至少两段点号）。")
        say("\n目标固定域名：https://%s   （以后永远不变）" % hostname)

        step(1, total, "准备本机 cloudflared")
        ensure_exe(CF_EXE, CF_WIN_URL, "cloudflared（Windows 版）")
        rc, out = cf(["--version"])
        say("   %s" % out.strip().splitlines()[0] if out.strip() else "   (版本未知)")

        step(2, total, "Cloudflare 授权（一次性）")
        ensure_login()

        step(3, total, "创建隧道并绑定域名")
        uid = ensure_tunnel(TUNNEL_NAME)
        ensure_dns(TUNNEL_NAME, hostname)

        step(4, total, "上传配置与凭据到服务器")
        push_to_server(r, uid, hostname)

        step(5, total, "重启隧道（切成命名隧道）")
        restart_tunnel(r)

        step(6, total, "公网实测")
        ok = verify_public(r, hostname)
        if ok:
            say("\n🎉 完成！固定域名已生效：https://%s" % hostname)
            say("   服务器重启 / 隧道掉线都会由 hub_guard.sh 自动拉起，域名不会变。")
            say("   建议紧接着做两件安全加固：")
            say("     1) .env 设 WEB_AUTH_REQUIRED=1 + WEB_ACCESS_TOKEN=强密码；")
            say("     2) Cloudflare 控制台 → Access → 给该域名加一层邮箱/Google 登录（免费 50 人）。")
            return 0
        say("\n⚠️ 配置已就位，但公网还没测通。按顺序自查：")
        say("   1) 域名 NS 是否已生效（Cloudflare 后台 Zone 是否 Active）；")
        say("   2) 源站是否在跑：curl http://127.0.0.1:8080/api/health（停了就先恢复服务）；")
        say("   3) 隧道日志：tail -50 %s" % CF_LOG)
        return 2
    finally:
        r.close()


if __name__ == "__main__":
    sys.exit(main())
