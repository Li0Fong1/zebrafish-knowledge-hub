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
    .venv\\Scripts\\python.exe -X utf8 scripts\\setup_named_tunnel.py --precheck            # 只做本地预检，不连服务器、不改动

2026-11-07：实际买到的固定域名是 **zebrafish-hub.top**（注意中间有连字符）。
它是**根域名**（只有一个点号），原来的校验 `hostname.count(".") < 2` 会把根域名
一律判为非法（那是给 hub.example.com 这类子域写的）。现已放开：根域名与子域名都支持，
并且不带 --hostname 时直接预填 zebrafish-hub.top。

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
# 2026-11-07 定名：Zebrafish Hub → zebrafish-hub.top（根域名，NS 托管在 Cloudflare）
DEFAULT_HOSTNAME = "zebrafish-hub.top"
# 顺手把 www 也指到同一条隧道，避免有人手动打 www 打不开（可选，失败不阻断）
WWW_HOSTNAME = "www." + DEFAULT_HOSTNAME

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


def www_ingress_block(www_host: str | None) -> str:
    """www 的 ingress 条目。

    只建 DNS 不写入 ingress 会掉进兜底规则：www 能解析、但一律 404 —— 实际踩过。
    所以要建就两边一起建、要退就两边一起退。
    """
    if not www_host:
        return ""
    lines = [
        "  # www 一并接入：避免有人手动打 www 打不开（只想留主域就把这段删掉）",
        "  - hostname: " + www_host,
        "    service: http://127.0.0.1:8080",
        "    originRequest:",
        "      connectTimeout: 30s",
        "      keepAliveTimeout: 90s",
        "      disableChunkedEncoding: false",
        "",
    ]
    return chr(10).join(lines)


def push_to_server(r: Remote, uid: str, hostname: str, www_host: str | None = None) -> None:
    # 先确认 CF_DIR 确实能当目录用。服务器上曾经有一个同名文件
    # （早期把 cloudflared 二进制直接放在 <RT>/cloudflared，39 MB ELF），
    # 这会让 mkdir 静默失败、config.yml 无处可写，后面全靠猜。
    probe = r.sh("if [ -e %s ] && [ ! -d %s ]; then echo notdir; else echo ok; fi"
                 % (CF_DIR, CF_DIR)).strip()
    if probe != "ok":
        die("服务器上 %s 是一个普通文件（很可能是早期放错位置的 cloudflared 二进制），\n"
            "   没法当配置目录用。先在服务器执行下面这条把它挪走，再重跑本脚本：\n"
            "     mv %s %s/cloudflared.legacy-$(date +%%Y%%m%%d) && mkdir -p %s"
            % (CF_DIR, CF_DIR, CF_BIN_DIR, CF_DIR))

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
    text = text.replace("REPLACE_WITH_WWW_INGRESS", www_ingress_block(www_host))
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
             "sed -n 's/^[[:space:]]*-[[:space:]]*hostname:[[:space:]]*//p' %s | head -1 | sed 's/^/  hostname: /'; "
             "else echo 'quick（快速隧道，随机域名，重启就换）'; fi; "
             "echo '=== 隧道进程 ==='; "
             "n=$(pgrep -cf 'cloudflared_run[ ]tunnel' || true); "
             "if [ \"${n:-0}\" = \"0\" ]; then echo '(无 —— 隧道当前未运行)'; "
             "else pgrep -af 'cloudflared_run[ ]tunnel' | cut -c1-150; fi; "
             "echo '=== 二进制位置 ==='; ls -l %s 2>/dev/null || echo '(持久目录里没有)'; "
             "ls -l /tmp/cloudflared_run 2>/dev/null || true"
             % (RT, CF_CONF, CF_CONF, CF_BIN), 90))


def quick_url(r: Remote) -> str:
    return r.sh("grep -oE 'https://[a-z0-9-]+[.]trycloudflare[.]com' %s 2>/dev/null | tail -1" % CF_LOG).strip()


def do_revert(r: Remote, reason: str = "") -> str:
    """退回快速隧道，并**立即**把隧道拉起来。

    2026-11-07 修：旧版只把 config.yml 改个名就完事。可进程还跑在命名模式里，
    守护看到「隧道还在跑」什么也不会做 —— 于是 config.yml 没了、流量却仍走旧隧道，
    等这条隧道一断，站点就彻底没入口。现在显式停进程 + 立刻起快速隧道。
    """
    if reason:
        say("   原因：%s" % reason)
    say("   停掉命名隧道进程（只动 cloudflared_run）")
    r.sh("pkill -f 'cloudflared_run[ ]tunnel' ; sleep 2; echo stopped")
    r.sh("mkdir -p %s && if [ -f %s ]; then mv %s %s.bak-$(date +%%Y%%m%%d%%H%%M%%S); fi; echo ok"
         % (CF_DIR, CF_CONF, CF_CONF, CF_CONF))
    say("   配置已改名留档（config.yml.bak-<时间戳>），需要时可改回来重试")
    say("   以快速隧道重新拉起…")
    r.sh("cd %s && setsid nohup %s tunnel --no-autoupdate --protocol http2 "
         "--url http://127.0.0.1:8080 >> %s 2>&1 </dev/null & sleep 8; echo started"
         % (RT, CF_BIN, CF_LOG))
    url = quick_url(r)
    say("   ✅ 已恢复公网访问：%s" % (url or "(还没拿到地址，看日志 tail -30 %s)" % CF_LOG))
    return url


# --------------------------------------------------------------------- main
# ---------------- 域名状态（RDAP + DNS，只读） ----------------
# 为什么要查 RDAP：域名被注册商暂停（client hold）时，DNS 只会返回 NXDOMAIN，
# 看起来像「域名不存在」，实际是「已注册但被停用」。两者的处理办法完全不同：
# 前者要重新买，后者只要完成实名认证就会自动解除。
BAD_DOMAIN_STATUS = ("client hold", "server hold", "pending delete", "redemption period")


def domain_state(host: str) -> dict:
    """返回 {registered, statuses, ns, ns_source, error}。registered=None 表示查不到。"""
    try:  # 复用 check_domain_ns 里已经验证过的 RDAP / DNS 双通道逻辑
        from check_domain_ns import query_rdap, query_ns_dns
    except ImportError:
        from scripts.check_domain_ns import query_rdap, query_ns_dns

    st = {"registered": None, "statuses": [], "ns": [], "ns_source": "", "error": ""}
    try:
        d = query_rdap(host)
        if d is None:
            st["registered"] = False
        elif d:
            st["registered"] = True
            st["statuses"] = [x.lower() for x in d.get("status", []) or []]
        else:
            st["error"] = "RDAP 无响应"
    except Exception as e:  # noqa: BLE001
        st["error"] = "RDAP 查询失败：%s" % e
    try:
        ns, src, _seen = query_ns_dns(host)
        st["ns"], st["ns_source"] = sorted(set(x for x in ns if x)), src
    except Exception as e:  # noqa: BLE001
        st["error"] = (st["error"] + "；" if st["error"] else "") + "NS 查询失败：%s" % e
    return st


def _on_cloudflare(st: dict) -> bool:
    return any("ns.cloudflare.com" in n for n in st["ns"])


def report_domain(host: str) -> dict:
    """打印域名状态并返回原始结果（供 precheck 与正式流程共用）。"""
    st = domain_state(host)
    if st["registered"] is False:
        say("    ✗ 注册局查无此域名 —— 还没买下，或刚买还没同步")
    elif st["registered"] is None:
        say("    ⚠️ 注册状态查不到（%s），只能看 NS" % (st["error"] or "原因未知"))
    else:
        say("    ✅ 已注册；状态码：%s" % (", ".join(st["statuses"]) or "(无)"))
        bad = [x for x in st["statuses"] if x in BAD_DOMAIN_STATUS]
        for x in bad:
            say("    ⛔ %s —— 域名被暂停解析，必须先在注册商解除" % x)
    if st["ns"]:
        say("    当前 NS（%s）：%s" % (st["ns_source"] or "?", ", ".join(st["ns"])))
        say("    指向 Cloudflare：%s" % ("是" if _on_cloudflare(st) else "否"))
    else:
        say("    当前 NS：查不到（域名被暂停时 DNS 必然查不到，属正常）")
    return st


def do_precheck(hostname: str | None) -> int:
    """买域名前的本地预检：不连服务器、不改动任何东西，可以反复跑。"""
    step(1, 1, "固定域名接入 · 本地预检（只读，不改服务器）")
    host = (hostname or DEFAULT_HOSTNAME).strip().lower()
    ok = True

    say("\n[1] 目标域名：%s" % host)
    if HOSTNAME_RE.match(host) and host.count(".") >= 1:
        say("    ✅ 格式合法（根域名 / 子域名均支持）")
    else:
        say("    ❌ 格式非法"); ok = False
    say("    主入口 https://%s" % host)
    say("    备用入口 https://www.%s" % host if not host.startswith("www.") else "    备用入口（已是 www）")

    say("\n[1.5] 域名现状（注册局 RDAP + DNS，只读）")
    ds = report_domain(host)

    say("\n[2] 本机 cloudflared")
    if CF_EXE.is_file() and CF_EXE.stat().st_size > 1_000_000:
        say("    ✅ 已就位：%s（%.1f MB）" % (CF_EXE, CF_EXE.stat().st_size / 1048576))
    else:
        say("    ⏳ 尚未下载，首次正式执行时会自动从 GitHub 下载（约 20 MB）")

    say("\n[3] Cloudflare 授权")
    if cf_login_ok():
        say("    ✅ 已授权（%s/cert.pem）" % CF_HOME)
    else:
        say("    ⏳ 尚未授权，正式执行时会打开浏览器让你登录并选中域名（仅一次）")

    say("\n[4] 配置模板")
    tpl = ROOT / "deploy" / "cloudflared" / "config.yml.template"
    if tpl.is_file():
        t = tpl.read_text(encoding="utf-8")
        placeholders = [p for p in ("REPLACE_WITH_TUNNEL_UUID",
                                    "REPLACE_WITH_CREDENTIALS_JSON_PATH",
                                    "REPLACE_WITH_HOSTNAME") if p in t]
        say("    ✅ 模板存在，占位符齐全：%s" % (", ".join(placeholders) or "(无)"))
        if len(placeholders) != 3:
            say("    ❌ 占位符数量不对"); ok = False
    else:
        say("    ❌ 模板缺失：%s" % tpl); ok = False

    say("\n[5] 服务器凭据读取")
    try:
        require_password()
        say("    ✅ .env 中 SSH 凭据可读（HOST=%s）" % HOST)
    except Exception as e:
        say("    ❌ 凭据读取失败：%s" % e); ok = False

    say("\n" + "=" * 78)
    if ok:
        bad = [x for x in ds["statuses"] if x in BAD_DOMAIN_STATUS]
        if ds["registered"] is False:
            say("本地准备就绪，但域名还没买下 —— 先去注册商购买，再回来。")
        elif bad:
            say("本地准备就绪，但域名被注册商暂停解析（%s）。" % ", ".join(bad))
            say("先完成实名认证解除暂停；此状态不解除，改 NS 也不会生效。")
        elif not _on_cloudflare(ds):
            say("本地准备就绪。还差两步必须由你完成：")
            say("   1) Cloudflare 后台 Add a site 填入 %s，拿到两条 NS；" % host)
            say("   2) 去注册商后台把 NS 换成那两条，等 Zone 变 Active。")
            say("然后回来跑一条命令：")
            say("      .venv\\Scripts\\python.exe -X utf8 scripts\\setup_named_tunnel.py --hostname %s" % host)
        else:
            say("✅ 全部就绪（NS 已在 Cloudflare）。直接跑：")
            say("   .venv\\Scripts\\python.exe -X utf8 scripts\\setup_named_tunnel.py --hostname %s" % host)
        say("   详见 docs/域名接入_zebrafish-hub.top.md")
    else:
        say("⚠️ 有未就绪项，请按上面提示补齐后重跑 --precheck")
    say("=" * 78)
    return 0 if ok else 2


def main() -> int:
    ap = argparse.ArgumentParser(description="给斑马鱼 Hub 配固定域名（Cloudflare 命名隧道）")
    ap.add_argument("--hostname", default=DEFAULT_HOSTNAME,
                    help="你要用的固定域名（根域名或子域名均可），默认 %s" % DEFAULT_HOSTNAME)
    ap.add_argument("--status", action="store_true", help="只看当前模式与域名")
    ap.add_argument("--revert", action="store_true", help="退回快速隧道（随机域名）")
    ap.add_argument("--precheck", action="store_true",
                    help="只做本地预检（域名格式 / 本机工具 / 授权状态 / 目标域名），不连服务器、不改动")
    ap.add_argument("--skip-domain-check", action="store_true",
                    help="跳过域名状态闸门（注册状态/NS 查不到但你想强行继续时用；风险自负）")
    args = ap.parse_args()

    if args.precheck:
        return do_precheck(args.hostname)

    r = Remote()
    try:
        if args.status:
            step(1, 1, "当前公网模式")
            show_status(r)
            return 0
        if args.revert:
            step(1, 1, "退回快速隧道")
            url = do_revert(r)
            say("   以后固定域名要恢复：重跑 --hostname <域名> 即可")
            say("   注意：快速隧道的域名是随机且会变的，只适合临时用。")
            if url:
                say("\n👉 当前可用地址：%s" % url)
            return 0

        total = 6
        hostname = (args.hostname or DEFAULT_HOSTNAME).strip().lower()
        if not hostname:
            hostname = DEFAULT_HOSTNAME
        # 根域名（a.b，1 个点号）与子域名（a.b.c，2 个点号）都合法。
        # 旧版要求 >=2 个点号，会把 zebrafish-hub.top 误判为非法。
        if not HOSTNAME_RE.match(hostname) or hostname.count(".") < 1:
            die("域名格式不对：应形如 zebrafish-hub.top 或 hub.example.com（至少一段点号）。")
        say("\n目标固定域名：https://%s   （以后永远不变）" % hostname)

        # 动 Cloudflare 之前先确认域名真能用。否则会出现最难排查的状态：
        # 隧道建好、凭据上传、config.yml 就位，但域名因为 client hold 或 NS 没换而根本解析不出来，
        # 守护还会每 60 秒「发现公网不通 → 重建隧道」，白白折腾。
        if args.skip_domain_check:
            say("   （已跳过域名状态闸门）")
        else:
            st = report_domain(hostname)
            bad = [x for x in st["statuses"] if x in BAD_DOMAIN_STATUS]
            if st["registered"] is False:
                die("注册局查无此域名。先确认域名已购买、且没拼错（注意连字符）。")
            if bad:
                die("域名被注册商暂停解析（%s），现在绑了也不会通。\n"
                    "   先完成实名认证解除暂停（注册商后台 → 域名列表 → 实名认证），再重跑本脚本。\n"
                    "   确认已完成仍报此状态：等 1 小时让注册局数据同步，或用 --skip-domain-check 强行继续。"
                    % ", ".join(bad))
            if not _on_cloudflare(st):
                die("NS 还没指向 Cloudflare（当前：%s）。\n"
                    "   命名隧道必须落在你自己 Cloudflare 账号下的 Zone 上：\n"
                    "   1) Cloudflare → Add a site → 填 %s → 选 Free；\n"
                    "   2) 记下它给的两条 NS，去注册商后台「修改 DNS 服务器」替换；\n"
                    "   3) 等 Zone 变 Active（几分钟到几小时），再重跑本脚本。"
                    % (", ".join(st["ns"]) or "查不到", hostname))

        step(1, total, "准备本机 cloudflared")
        ensure_exe(CF_EXE, CF_WIN_URL, "cloudflared（Windows 版）")
        rc, out = cf(["--version"])
        say("   %s" % out.strip().splitlines()[0] if out.strip() else "   (版本未知)")

        step(2, total, "Cloudflare 授权（一次性）")
        ensure_login()

        step(3, total, "创建隧道并绑定域名")
        uid = ensure_tunnel(TUNNEL_NAME)
        ensure_dns(TUNNEL_NAME, hostname)
        # www 是可选增值项：zone 里没这个记录、或已是别的服务，都不该阻断主域名绑定
        www = ("www." + hostname) if not hostname.startswith("www.") else None
        www_ok: str | None = None
        if www:
            rc, out = cf(["tunnel", "route", "dns", TUNNEL_NAME, www])
            if rc == 0 or "already exists" in out.lower():
                www_ok = www
                say("   ✅ www 也已指向隧道：%s" % www)
            else:
                say("   ⚠️ www 未绑定（不影响主域名）：%s" % out.strip().splitlines()[0][:120])

        step(4, total, "上传配置与凭据到服务器")
        push_to_server(r, uid, hostname, www_ok)

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
        say("\n⚠️ 配置已就位，但固定域名还没测通。按顺序自查：")
        say("   1) 域名 NS 是否已生效（Cloudflare 后台 Zone 是否 Active）；")
        say("   2) 源站是否在跑：curl http://127.0.0.1:8080/api/health（停了就先恢复服务）；")
        say("   3) 隧道日志：tail -50 %s" % CF_LOG)
        say("\n   站点不能一直空着，先自动退回快速隧道保证还能访问。")
        say("   NS 生效后重跑同一条 --hostname 命令即可（脚本可重复执行，会复用隧道与 DNS）。")
        url = do_revert(r, "固定域名未通，避免站点失联")
        say("\n👉 当前可用地址：%s" % (url or "见 %s/public_url.txt" % RT))
        return 2
    finally:
        r.close()


if __name__ == "__main__":
    sys.exit(main())
