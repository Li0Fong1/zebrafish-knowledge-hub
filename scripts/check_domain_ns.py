# -*- coding: utf-8 -*-
"""域名接入前的只读体检：这个域名现在到底能不能用。

用法（在项目根目录）：
    .venv\\Scripts\\python.exe -X utf8 scripts\\check_domain_ns.py
    .venv\\Scripts\\python.exe -X utf8 scripts\\check_domain_ns.py zebrafish-hub.top

为什么先查 RDAP 而不是只查 DNS：
    域名被注册商暂停（client hold）时，DNS 查询只会返回 NXDOMAIN —— 看起来像「域名没买」，
    很容易误判。注册局 RDAP 里同时有「注册状态 / 注册商 / 权威 NS」，能一眼看出是
    没注册、没实名被暂停，还是已注册但 NS 还没改。

只读、不连服务器、不改动任何东西；可以反复跑。
退出码：0=已就绪（NS 在 Cloudflare）；1=已注册但还差步骤；2=未注册。
"""
from __future__ import annotations

import json
import ssl
import subprocess
import sys
import urllib.error
import urllib.request

DEFAULT = "zebrafish-hub.top"

# 注册局状态码 → 人话。只解释常见的几个，其余原样打印。
STATUS_NOTES = {
    "client hold": (
        "注册商暂停解析 —— 域名现在完全不可用（DNS 查不到就是这个原因）。",
        "在国内注册商新注册的域名，绝大多数是「实名认证未完成」。去注册商后台完成实名认证"
        "（个人身份证 / 企业营业执照），通过后这个状态会自动消失，通常几分钟到几小时。"
        "这一步不做，NS 改成什么都没用。",
    ),
    "server hold": (
        "注册局暂停解析 —— 域名被注册局层面挂起。",
        "多为实名认证逾期未通过。先联系注册商客服确认原因。",
    ),
    "add period": ("新注册宽限期 —— 正常状态，不影响使用。", None),
    "client transfer prohibited": ("注册 60 天内禁止转移注册商 —— 正常，不影响使用。", None),
    "client renew prohibited": ("新注册期禁止续费 —— 正常，到期前会解除。", None),
    "ok": ("正常。", None),
    "active": ("正常。", None),
}

BAD_STATUS = ("client hold", "server hold", "pending delete", "redemption period")


def _http_json(url: str, headers: dict | None = None, timeout: int = 25) -> dict:
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "Mozilla/5.0"})
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def query_rdap(domain: str) -> dict | None:
    """注册局 RDAP。返回 None 表示域名未注册（404），{} 表示查询失败。"""
    try:
        return _http_json("https://rdap.org/domain/" + domain)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
    except Exception:  # noqa: BLE001
        pass
    return {}


def _rdap_entities(d: dict) -> list[tuple[str, str]]:
    """[(role, 名称)]，用来取注册商/注册人。"""
    out = []
    for ent in d.get("entities", []) or []:
        roles = ", ".join(ent.get("roles") or [])
        name = ""
        v = ent.get("vcardArray")
        if isinstance(v, list) and len(v) > 1:
            for item in v[1]:
                if isinstance(item, list) and item and item[0] == "fn":
                    name = item[3] if len(item) > 3 else ""
                    break
        if roles:
            out.append((roles, name))
    return out


def _via_doh(name: str) -> list[str]:
    """Cloudflare DNS-over-HTTPS。部分网络会拦该端点，失败就降级。"""
    data = _http_json("https://cloudflare-dns.com/dns-query?name=%s&type=NS" % name,
                      {"accept": "application/dns-json"}, timeout=12)
    return [a.get("data", "").rstrip(".").lower() for a in data.get("Answer", [])]


def _via_nslookup(name: str, server: str | None = None) -> list[str]:
    """系统 nslookup（Windows / Linux 都有）。server=None 用系统默认解析器。

    单独看默认解析器会被缓存骗：注册局刚改完，本地递归可能还挂着旧 NS（TTL 常 1~48h）。
    所以下面 query_ns_dns 会同时问几个公共解析器，任一看到 Cloudflare 就算已切换。
    """
    args = ["nslookup", "-type=NS", name] + ([server] if server else [])
    p = subprocess.run(args, capture_output=True,
                       text=True, timeout=20, encoding="utf-8", errors="replace")
    out = (p.stdout or "") + (p.stderr or "")
    ns = []
    for line in out.splitlines():
        low = line.lower()
        if "nameserver" in low and "=" in line:
            ns.append(line.split("=", 1)[1].strip().rstrip(".").lower())
        elif "nameserver" in low and "ns" in low:
            parts = line.split()
            for i, tok in enumerate(parts):
                if tok.lower() == "nameserver" and i + 1 < len(parts):
                    ns.append(parts[i + 1].rstrip(".").lower())
    return ns


RESOLVERS = ("223.5.5.5", "119.29.29.29", "8.8.8.8")


def query_ns_dns(name: str) -> tuple[list[str], str, list[tuple[str, list[str]]]]:
    """多通道查询 NS，返回 (合并结果, 来源说明, [(通道, 该通道看到的值)])。

    为什么合并：注册局改 NS 后，各递归解析器的缓存到期时间不同，
    只看一个通道会得出"还没改"的错误结论。任一通道看到 Cloudflare 即认为已切换。
    """
    seen: list[tuple[str, list[str]]] = []
    try:
        seen.append(("Cloudflare DoH", [x for x in _via_doh(name) if x]))
    except Exception:  # noqa: BLE001
        pass
    for srv in RESOLVERS:
        try:
            got = [x for x in _via_nslookup(name, srv) if x]
            if got:
                seen.append((srv, got))
        except Exception:  # noqa: BLE001
            continue
    try:
        got = [x for x in _via_nslookup(name) if x]
        if got:
            seen.append(("默认解析器", got))
    except Exception:  # noqa: BLE001
        pass

    merged: set[str] = set()
    for _, v in seen:
        merged.update(v)
    src = " + ".join(k for k, _ in seen) if seen else "查询失败"
    return sorted(merged), src, seen


def _fmt(ts: str | None) -> str:
    if not ts:
        return "(未知)"
    return ts.replace("T", " ").replace("Z", " UTC")[:19] + " UTC"


def main() -> int:
    domain = (sys.argv[1] if len(sys.argv) > 1 else DEFAULT).strip().lower().rstrip(".")
    print("域名：%s\n" % domain)

    print("=== 一、注册状态（注册局 RDAP，权威） ===")
    d = query_rdap(domain)
    if d is None:
        print("✗ 未注册：注册局查无此域名，可以直接去注册商购买。")
        return 2
    if not d:
        print("⚠️ RDAP 查询失败（本机网络可能限制），只能看下面的 DNS 结果。")
    else:
        status = [s.lower() for s in d.get("status", []) or []]
        events = {e.get("eventAction"): e.get("eventDate") for e in d.get("events", []) or []}
        print("  注册时间：%s" % _fmt(events.get("registration")))
        print("  到期时间：%s" % _fmt(events.get("expiration")))
        for role, name in _rdap_entities(d):
            if "registrar" in role:
                print("  注册商：%s" % (name or "(未标注)"))
            elif "registrant" in role:
                print("  注册人：%s" % (name or "(隐私保护)"))
        print("  状态码：%s" % (", ".join(status) or "(无)"))
        bad = [s for s in status if s in BAD_STATUS]
        if bad:
            print()
            for s in bad:
                title, hint = STATUS_NOTES.get(s, ("", None))
                print("  ⛔ %s —— %s" % (s, title))
                if hint:
                    print("     %s" % hint)
            print("\n  ➜ 结论：域名已注册但被暂停，现在解析不出来。")
            print("     先解决上面的状态，再谈 NS 和隧道；顺序反了会白折腾。")
        reg_ns = [n.get("ldhName", "").rstrip(".").lower()
                  for n in d.get("nameservers", []) or [] if n.get("ldhName")]
        if reg_ns:
            print("\n  注册局记录的 NS：")
            for n in sorted(set(reg_ns)):
                print("    %s" % n)

    print("\n=== 二、实际解析（DNS 查询） ===")
    ns, src, seen = query_ns_dns(domain)
    ns = sorted(set(x for x in ns if x))
    print("  查询方式：%s" % src)
    # 通道之间不一致时点明：这通常意味着"注册局已改、部分递归还没过期"，
    # 看到旧 NS 不代表改错了，别让人白白回注册商再改一遍。
    if len(seen) > 1 and len({tuple(v) for _, v in seen}) > 1:
        print("  ⚠️ 各通道结果不一致（缓存未同步）：")
        for k, v in seen:
            print("     %-16s %s" % (k, ", ".join(sorted(set(v)))))
    if ns:
        for n in ns:
            print("    %s" % n)
    else:
        print("  （查不到 NS 记录）")

    if any("ns.cloudflare.com" in n for n in ns):
        print("\n✅ NS 已指向 Cloudflare。")
        print("   确认后台 Zone 状态为 Active 后，跑：")
        print("   .venv\\Scripts\\python.exe -X utf8 scripts\\setup_named_tunnel.py --hostname %s" % domain)
        return 0

    if ns:
        print("\n⏳ 域名已注册，但 NS 还没改到 Cloudflare。")
        print("   1) Cloudflare 后台「添加站点」→ 填入 %s → 选免费计划" % domain)
        print("   2) 它会给你两条 NS（形如 xxx.ns.cloudflare.com）")
        print("   3) 去注册商后台「修改 DNS 服务器」填这两条，保存")
        print("   4) 等 Zone 变 Active（常见 5 分钟～24 小时），再跑一次本脚本")
        return 1

    print("\n⚠️ 域名已注册，但当前查不到任何 NS —— 通常就是被上面的暂停状态挡住了。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
