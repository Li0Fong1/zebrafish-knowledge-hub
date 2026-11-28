# -*- coding: utf-8 -*-
"""查看网站「反馈」按钮提交上来的消息（项目负责人侧）。

背景
----
``POST /api/hub/feedback`` 是一个**只写不读**的接口：服务端把反馈追加写入
``$ZF_RUNTIME/feedback/feedback.jsonl``，既没有 GET 列表接口，网页上也没有任何
查看入口，所以提交后页面上只会显示「已收到」，之后就再也看不到了。
本脚本补上查看通道，且完全不改动线上网页、不新增功能页、不向访客暴露任何内容。

用法
----
    python scripts/read_feedback.py                     # 打印最近 30 条 + 生成 HTML 报告
    python scripts/read_feedback.py --all              # 打印全部
    python scripts/read_feedback.py --no-selftest      # 隐藏自检测试数据
    python scripts/read_feedback.py --out out.html     # 指定 HTML 报告路径
    python scripts/read_feedback.py --md out.md        # 额外导出 Markdown
    python scripts/read_feedback.py --host <服务器地址> # 临时指定主机

凭据沿用项目约定：环境变量 > 仓库根 ``.env`` 的
``ZF_HOST / ZF_PORT / ZF_USER / ZF_PASS / ZF_RUNTIME``；源码内不硬编码任何密码。
若 ``ZF_HOST``（域名）连不上，会自动尝试 ``ZF_HOST_IP`` 里配置的直连地址。
"""
from __future__ import annotations

import argparse
import html
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts._zf_creds import PORT, USER, require_password  # noqa: E402

REMOTE_PATH_TPL = "{runtime}/feedback/feedback.jsonl"
DEFAULT_HTML = ROOT / "output" / "反馈记录.html"


def env(name: str, default: str = "") -> str:
    v = (os.environ.get(name) or "").strip()
    if v:
        return v
    p = ROOT / ".env"
    if p.is_file():
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, val = line.split("=", 1)
            if k.strip() == name:
                return val.strip().strip('"').strip("'")
    return default


def connect(host: str, port: int, user: str, pwd: str) -> paramiko.SSHClient:
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    cli.connect(host, port=port, username=user, password=pwd, timeout=20,
                banner_timeout=30, auth_timeout=30,
                allow_agent=False, look_for_keys=False)
    cli.get_transport().set_keepalive(15)
    return cli


def fetch(host_arg: str) -> tuple[list[dict], str]:
    pwd = require_password()
    user = env("ZF_USER") or USER
    port = int(env("ZF_PORT") or PORT or 22)
    runtime = env("ZF_RUNTIME") or "/data/zoujin/deng/zebrafish_runtime"
    remote_path = REMOTE_PATH_TPL.format(runtime=runtime.rstrip("/"))

    hosts = [host_arg or env("ZF_HOST")]
    fallback = env("ZF_HOST_IP")
    if fallback and fallback not in hosts:
        hosts.append(fallback)

    last_err: Exception | None = None
    for host in hosts:
        if not host:
            continue
        try:
            cli = connect(host, port, user, pwd)
            _, stdout, stderr = cli.exec_command(
                "cat " + remote_path + " 2>/dev/null", timeout=30)
            raw = stdout.read().decode("utf-8", "replace")
            err = stderr.read().decode("utf-8", "replace").strip()
            cli.close()
            if not raw.strip():
                # 文件不存在或为空，都是「暂时没有反馈」，不算错误
                return [], host
            rows = []
            for line in raw.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    rows.append({"at": "", "message": line, "contact": "", "page": ""})
            return rows, host
        except Exception as e:  # noqa: BLE001 - 换下一个候选主机
            last_err = e
            continue
    raise SystemExit(f"连接服务器失败：{last_err}")


def is_selftest(r: dict) -> bool:
    c = str(r.get("contact") or "").lower()
    m = str(r.get("message") or "")
    return c == "selftest" or m.startswith("自检：")


def print_rows(rows: list[dict], limit: int) -> None:
    if not rows:
        print("（目前服务器上还没有任何反馈记录）")
        return
    shown = rows if limit <= 0 else rows[-limit:]
    print(f"共 {len(rows)} 条，显示最近 {len(shown)} 条：\n")
    for i, r in enumerate(shown, 1):
        tag = " [自检]" if is_selftest(r) else ""
        print(f"{i:>3}. {r.get('at') or '时间未知'}{tag}")
        print(f"     内容：{r.get('message') or ''}")
        if r.get("contact"):
            print(f"     联系方式：{r['contact']}")
        if r.get("page"):
            print(f"     来自页面：{r['page']}")
        print()


def render_html(rows: list[dict], no_selftest: bool, host: str) -> str:
    data = [r for r in rows if not (no_selftest and is_selftest(r))]
    real = [r for r in rows if not is_selftest(r)]
    cards = []
    for r in reversed(data):
        tag = ('<span class="tag test">自检数据</span>'
               if is_selftest(r) else '<span class="tag real">用户反馈</span>')
        contact = html.escape(str(r.get("contact") or ""))
        page = html.escape(str(r.get("page") or ""))
        msg = html.escape(str(r.get("message") or "")).replace("\n", "<br>")
        at = html.escape(str(r.get("at") or "时间未知"))
        meta = []
        if contact:
            meta.append(f"<span>联系方式：<b>{contact}</b></span>")
        if page:
            meta.append(f"<span>页面：{page}</span>")
        cards.append(
            f'<article class="card"><header>{tag}<time>{at}</time></header>'
            f'<p class="msg">{msg}</p>'
            f'<footer>{"".join(meta) or "<span>未留联系方式</span>"}</footer></article>'
        )
    body = "\n".join(cards) or '<div class="empty">目前还没有反馈记录</div>'
    generated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>用户反馈记录</title>
<style>
  *{{box-sizing:border-box}}
  body{{margin:0;padding:32px 20px 60px;background:#eaf4fb;color:#123a52;
       font:15px/1.7 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}}
  .wrap{{max-width:820px;margin:0 auto}}
  h1{{font-size:24px;margin:0 0 6px;color:#0b5b83}}
  .sub{{color:#5c7d91;font-size:13px;margin-bottom:22px}}
  .stats{{display:flex;gap:12px;margin-bottom:24px;flex-wrap:wrap}}
  .stat{{background:#fff;border:1px solid #cfe4f1;border-radius:12px;
        padding:12px 20px;min-width:130px;box-shadow:0 2px 10px rgba(16,106,148,.06)}}
  .stat b{{display:block;font-size:24px;color:#106a94;line-height:1.2}}
  .stat span{{font-size:12px;color:#6a8a9c}}
  .card{{background:#fff;border:1px solid #d8eaf4;border-radius:14px;
        padding:16px 20px;margin-bottom:14px;box-shadow:0 3px 14px rgba(16,106,148,.07)}}
  .card header{{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px}}
  .card time{{font-size:12px;color:#7c99a9}}
  .tag{{font-size:12px;padding:3px 10px;border-radius:20px}}
  .tag.real{{background:#e0f2ff;color:#0b6a97}}
  .tag.test{{background:#f1f3f5;color:#7b8790}}
  .msg{{margin:0 0 12px;font-size:15px;white-space:normal;word-break:break-word}}
  .card footer{{border-top:1px dashed #e2eef5;padding-top:9px;display:flex;
               gap:18px;flex-wrap:wrap;font-size:12px;color:#6a8a9c}}
  .empty{{background:#fff;border-radius:14px;padding:40px;text-align:center;color:#7c99a9}}
  .note{{margin-top:26px;font-size:12px;color:#7896a7;line-height:1.8}}
</style></head><body><div class="wrap">
<h1>用户反馈记录</h1>
<div class="sub">来源：网站右下角「反馈」按钮 · 每次运行本页工具时从服务器实时拉取</div>
<div class="stats">
  <div class="stat"><b>{len(rows)}</b><span>全部记录</span></div>
  <div class="stat"><b>{len(real)}</b><span>真实用户反馈</span></div>
  <div class="stat"><b>{generated}</b><span>本次拉取时间</span></div>
</div>
{body}
<div class="note">
  数据存放位置：服务器 <code>{html.escape(host)}</code> 的
  <code>$ZF_RUNTIME/feedback/feedback.jsonl</code>（逐行 JSON，只追加写入）。<br>
  本站不采集 IP / 账号；联系方式与页面地址均为提交者主动填写。此页面为本地文件，不会发布到网站。
</div>
</div></body></html>"""


def render_md(rows: list[dict], no_selftest: bool) -> str:
    data = [r for r in rows if not (no_selftest and is_selftest(r))]
    lines = ["# 用户反馈记录", "",
             f"共 {len(data)} 条（拉取时间 {datetime.now():%Y-%m-%d %H:%M:%S}）", ""]
    for r in reversed(data):
        tag = "自检" if is_selftest(r) else "用户"
        lines.append(f"## [{tag}] {r.get('at') or '时间未知'}")
        lines.append("")
        lines.append(str(r.get("message") or "").replace("\n", "  \n"))
        lines.append("")
        if r.get("contact"):
            lines.append(f"- 联系方式：{r['contact']}")
        if r.get("page"):
            lines.append(f"- 页面：{r['page']}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="查看网站 Feedback 提交的用户反馈")
    ap.add_argument("--host", default="", help="覆盖 .env 里的 ZF_HOST")
    ap.add_argument("--all", action="store_true", help="打印全部（默认最近 30 条）")
    ap.add_argument("--limit", type=int, default=30, help="打印条数，0 表示全部")
    ap.add_argument("--no-selftest", action="store_true", help="报告中隐藏自检测试数据")
    ap.add_argument("--out", default=str(DEFAULT_HTML), help="HTML 报告输出路径")
    ap.add_argument("--no-html", action="store_true", help="不生成 HTML 报告")
    ap.add_argument("--md", default="", help="同时导出 Markdown 到指定路径")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    rows, host = fetch(args.host)
    limit = 0 if args.all else args.limit
    console_rows = [r for r in rows if not (args.no_selftest and is_selftest(r))]
    print_rows(console_rows, limit)

    if not args.no_html:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render_html(rows, args.no_selftest, host), encoding="utf-8")
        print(f"HTML 报告：{out}")
    if args.md:
        md = Path(args.md)
        md.parent.mkdir(parents=True, exist_ok=True)
        md.write_text(render_md(rows, args.no_selftest), encoding="utf-8")
        print(f"Markdown：{md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
