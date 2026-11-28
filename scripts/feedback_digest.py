# -*- coding: utf-8 -*-
"""用户反馈周期汇总（每 72 小时一封邮件）的内容引擎 + 可选 SMTP 直发。

与 `read_feedback.py` 的分工
----------------------------
* `read_feedback.py`  —— 人想看时手动拉一次。
* `feedback_digest.py` —— 给定时任务用：按「上次已发送水位」挑出新增反馈，
  生成可直接作为邮件正文的 HTML / 纯文本，发送成功后才推进水位，
  因此发信失败不会丢记录、下期也不会重复投递。

标准流程（由 WorkBuddy 定时任务驱动）
------------------------------------
    python scripts/feedback_digest.py --prepare       # 1. 生成本期汇总
    <用 Agent Mail 把 latest.json 里 body_html 指向的文件发出去>
    python scripts/feedback_digest.py --mark-sent     # 2. 确认发出后推进水位

如果配置了 SMTP（.env 里 SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASS），
也可以自己发完，不依赖任何会话工具：
    python scripts/feedback_digest.py --check-smtp            # 先验证授权码（不发信）
    python scripts/feedback_digest.py --prepare --send-smtp   # 生成并发送
    python scripts/feedback_digest.py --send-smtp             # 复用 2 小时内的 latest.json 发送

`--send-smtp` 只有在发送成功后才调用 `--mark-sent`；未配置或发送失败时
水位保持不动，下期会重新汇总，不会漏发。

水位文件 `output/.feedback_digest_state.json` 只记录「最后一条已发送记录的指纹」，
不保存反馈正文；重置用 `--reset`。
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import smtplib
import sys
from datetime import datetime, timedelta
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.read_feedback import (  # noqa: E402
    env, fetch, is_selftest,
)

OUT_DIR = ROOT / "output" / "feedback-digest"
STATE = ROOT / "output" / ".feedback_digest_state.json"
LATEST = OUT_DIR / "latest.json"
PERIOD_HOURS = 72
DEFAULT_TO = "2959566724@qq.com"


# --------------------------------------------------------------------------- 水位

def fp(rec: dict) -> str:
    raw = "|".join(str(rec.get(k) or "") for k in ("at", "message", "contact", "page"))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def load_state() -> dict:
    if STATE.is_file():
        try:
            return json.loads(STATE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"last_sent_at": None, "last_sent_record": None, "cycles": 0, "pending": None}


def save_state(st: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------- 内容

def build_html(new_rows: list[dict], real_new: list[dict], all_rows: list[dict],
               host: str, first_run: bool, generated: datetime) -> str:
    real_all = [r for r in all_rows if not is_selftest(r)]
    period = ("首次汇总（含全部历史记录）" if first_run else
              f"上次发送以来的新增反馈（周期 {PERIOD_HOURS} 小时）")
    cards = []
    for r in reversed(real_new):
        at = html.escape(str(r.get("at") or "时间未知"))
        msg = html.escape(str(r.get("message") or "")).replace("\n", "<br>")
        contact = html.escape(str(r.get("contact") or ""))
        page = html.escape(str(r.get("page") or ""))
        rows = [f'<div style="color:#5c7d91;font-size:12px;margin-top:8px">'
                f'时间：{at}</div>']
        if contact:
            rows.append(f'<div style="color:#5c7d91;font-size:12px;margin-top:4px">'
                        f'联系方式：<b style="color:#123a52">{contact}</b></div>')
        if page:
            rows.append(f'<div style="color:#5c7d91;font-size:12px;margin-top:4px;'
                        f'word-break:break-all">来源页面：{page}</div>')
        cards.append(
            '<div style="background:#ffffff;border:1px solid #d8eaf4;border-radius:10px;'
            'padding:16px 18px;margin:0 0 12px">'
            f'<div style="font-size:15px;color:#123a52;line-height:1.7">{msg}</div>'
            + "".join(rows) + "</div>"
        )
    if not cards:
        cards.append(
            '<div style="background:#ffffff;border:1px solid #d8eaf4;border-radius:10px;'
            'padding:26px;text-align:center;color:#7c99a9;font-size:14px">'
            '本周期没有新增的用户反馈。</div>'
        )

    def stat(n: int, label: str) -> str:
        return (f'<td width="33%" style="padding:0 6px 0 0">'
                f'<div style="background:#ffffff;border:1px solid #cfe4f1;border-radius:10px;'
                f'padding:14px 16px"><div style="font-size:24px;color:#106a94;'
                f'font-weight:700;line-height:1.2">{n}</div>'
                f'<div style="font-size:12px;color:#6a8a9c;margin-top:2px">{label}</div>'
                f'</div></td>')

    nxt = (generated + timedelta(hours=PERIOD_HOURS)).strftime("%Y-%m-%d %H:%M")
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:24px 12px;background:#eaf4fb;
 font:15px/1.7 -apple-system,'Segoe UI','Microsoft YaHei',sans-serif;color:#123a52">
<div style="max-width:640px;margin:0 auto">
  <h1 style="font-size:21px;color:#0b5b83;margin:0 0 6px">斑马鱼 Hub · 用户反馈汇总</h1>
  <div style="color:#5c7d91;font-size:13px;margin-bottom:20px">
    生成时间 {generated:%Y-%m-%d %H:%M} · 统计口径：{period}</div>
  <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:20px"><tr>
    {stat(len(real_new), '本周期新增反馈')}
    {stat(len(real_all), '累计真实反馈')}
    {stat(len(all_rows), '服务器全部记录')}
  </tr></table>
  <div style="font-size:14px;color:#0b5b83;font-weight:600;margin:0 0 10px">本周期反馈明细</div>
  {''.join(cards)}
  <div style="margin-top:22px;padding-top:14px;border-top:1px dashed #cfe4f1;
       font-size:12px;color:#7896a7;line-height:1.9">
    数据来源：网站右下角「反馈」按钮，服务器 {html.escape(host)}
    的 <code>$ZF_RUNTIME/feedback/feedback.jsonl</code>。<br>
    自检测试数据不计入明细；联系方式与页面地址均为提交者主动填写，本站不采集 IP / 账号。<br>
    本邮件每 {PERIOD_HOURS} 小时自动发送一次，下次预计 {nxt}。
  </div>
</div></body></html>"""


def build_text(new_rows: list[dict], real_new: list[dict], all_rows: list[dict],
               first_run: bool, generated: datetime) -> str:
    real_all = [r for r in all_rows if not is_selftest(r)]
    lines = [
        "斑马鱼 Hub · 用户反馈汇总",
        f"生成时间：{generated:%Y-%m-%d %H:%M}",
        f"本周期新增反馈：{len(real_new)} 条",
        f"累计真实反馈：{len(real_all)} 条 / 服务器全部记录：{len(all_rows)} 条",
        "",
    ]
    if first_run:
        lines.append("（首次汇总，以下含全部历史记录中的真实反馈）")
        lines.append("")
    if not real_new:
        lines.append("本周期没有新增的用户反馈。")
    for i, r in enumerate(reversed(real_new), 1):
        lines.append(f"{i}. [{r.get('at') or '时间未知'}] {r.get('message') or ''}")
        if r.get("contact"):
            lines.append(f"   联系方式：{r['contact']}")
        if r.get("page"):
            lines.append(f"   来源页面：{r['page']}")
        lines.append("")
    lines.append(f"下次汇总预计：{generated + timedelta(hours=PERIOD_HOURS):%Y-%m-%d %H:%M}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- 命令

def prepare() -> dict:
    rows, host = fetch("")
    st = load_state()
    last = st.get("last_sent_record")
    first_run = not last
    if last:
        idx = next((i for i, r in enumerate(rows) if fp(r) == last), -1)
        new_rows = rows[idx + 1:] if idx >= 0 else rows
        watermark_lost = idx < 0
    else:
        new_rows, watermark_lost = rows, False
    real_new = [r for r in new_rows if not is_selftest(r)]
    generated = datetime.now()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stem = "digest_" + generated.strftime("%Y%m%d_%H%M")
    html_path = OUT_DIR / (stem + ".html")
    text_path = OUT_DIR / (stem + ".txt")
    html_body = build_html(new_rows, real_new, rows, host, first_run, generated)
    text_body = build_text(new_rows, real_new, rows, first_run, generated)
    html_path.write_text(html_body, encoding="utf-8")
    text_path.write_text(text_body, encoding="utf-8")

    subject = (f"【斑马鱼 Hub】用户反馈汇总 {generated:%Y-%m-%d} "
               f"· 新增 {len(real_new)} 条")
    meta = {
        "subject": subject,
        "to": env("FEEDBACK_DIGEST_TO", DEFAULT_TO),
        "body_html": str(html_path),
        "body_text": str(text_path),
        "html_inline": html_body,
        "generated_at": generated.strftime("%Y-%m-%d %H:%M:%S"),
        "first_run": first_run,
        "watermark_lost": watermark_lost,
        "new_count": len(real_new),
        "new_raw_count": len(new_rows),
        "total_count": len(rows),
        "real_total": len([r for r in rows if not is_selftest(r)]),
        "period_hours": PERIOD_HOURS,
        "next_at": (generated + timedelta(hours=PERIOD_HOURS)).strftime("%Y-%m-%d %H:%M"),
        "server": host,
    }
    LATEST.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    st["pending"] = {
        "generated_at": meta["generated_at"],
        "last_record_fp": fp(rows[-1]) if rows else None,
        "new_count": len(real_new),
        "subject": subject,
    }
    save_state(st)

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print(f"主题：{subject}")
    print(f"收件：{meta['to']}")
    print(f"本周期新增真实反馈：{len(real_new)} 条（原始新增 {len(new_rows)} 条）")
    print(f"累计：真实 {meta['real_total']} 条 / 全部 {len(rows)} 条"
          + (" · 首次汇总" if first_run else "")
          + (" · 注意：水位记录未匹配，已回退为全量" if watermark_lost else ""))
    print(f"HTML：{html_path}")
    print(f"文本：{text_path}")
    print(f"元信息：{LATEST}")
    return meta


def mark_sent() -> None:
    st = load_state()
    pending = st.get("pending")
    if not pending:
        raise SystemExit("没有待确认的汇总，请先运行 --prepare")
    st["last_sent_at"] = pending["generated_at"]
    st["last_sent_record"] = pending["last_record_fp"]
    st["cycles"] = int(st.get("cycles") or 0) + 1
    st["pending"] = None
    save_state(st)
    print(f"水位已推进：{st['last_sent_at']}（累计发送 {st['cycles']} 期）")


def smtp_conf() -> tuple[str, int, str, str]:
    host, user, pwd = env("SMTP_HOST"), env("SMTP_USER"), env("SMTP_PASS")
    if not (host and user and pwd):
        raise SystemExit(
            "未配置 SMTP：请在 .env 中设置 SMTP_HOST / SMTP_PORT / SMTP_USER / "
            "SMTP_PASS（QQ 邮箱用授权码，不是登录密码）"
        )
    return host, int(env("SMTP_PORT", "465") or 465), user, pwd


def smtp_login(host: str, port: int, user: str, pwd: str):
    if port == 465:
        srv = smtplib.SMTP_SSL(host, port, timeout=30)
    else:
        srv = smtplib.SMTP(host, port, timeout=30)
        srv.ehlo()
        srv.starttls()
        srv.ehlo()
    srv.login(user, pwd)
    return srv


def check_smtp() -> None:
    """只验证连通与登录，不发送任何邮件。拿到授权码后先跑这个。"""
    host, port, user, pwd = smtp_conf()
    srv = smtp_login(host, port, user, pwd)
    try:
        srv.quit()
    except Exception:
        pass
    print(f"SMTP 自检通过：{user} @ {host}:{port}（仅登录验证，未发送邮件）")


def send_smtp(meta: dict) -> None:
    host, port, user, pwd = smtp_conf()
    to = meta["to"]
    msg = MIMEMultipart("alternative")
    msg["Subject"] = Header(meta["subject"], "utf-8").encode()
    msg["From"] = user
    msg["To"] = to
    msg.attach(MIMEText(Path(meta["body_text"]).read_text(encoding="utf-8"),
                        "plain", "utf-8"))
    msg.attach(MIMEText(Path(meta["body_html"]).read_text(encoding="utf-8"),
                        "html", "utf-8"))
    srv = smtp_login(host, port, user, pwd)
    try:
        srv.sendmail(user, [to], msg.as_string())
    finally:
        try:
            srv.quit()
        except Exception:
            pass
    print(f"SMTP 已发送：{to} | {meta['subject']}")


def main() -> int:
    ap = argparse.ArgumentParser(description="用户反馈 72 小时周期汇总")
    ap.add_argument("--prepare", action="store_true", help="生成本期汇总（不推进水位）")
    ap.add_argument("--mark-sent", action="store_true", help="确认已发出后推进水位")
    ap.add_argument("--send-smtp", action="store_true", help="用 .env 的 SMTP 配置直接发送")
    ap.add_argument("--check-smtp", action="store_true", help="只验证 SMTP 连通与登录，不发信")
    ap.add_argument("--reset", action="store_true", help="重置水位（下期重发全量）")
    ap.add_argument("--status", action="store_true", help="查看当前水位状态")
    args = ap.parse_args()

    if args.check_smtp:
        check_smtp()
        return 0
    if args.reset:
        save_state({"last_sent_at": None, "last_sent_record": None,
                    "cycles": 0, "pending": None})
        print("水位已重置")
        return 0
    if args.status:
        print(json.dumps(load_state(), ensure_ascii=False, indent=2))
        return 0
    if args.mark_sent:
        mark_sent()
        return 0
    if not (args.prepare or args.send_smtp):
        ap.print_help()
        return 0

    # --send-smtp 单独使用时复用刚生成的 latest.json（定时任务先 --prepare），
    # 避免同一期生成两份文件；超过 2 小时视为过期，重新生成。
    meta = None
    if not args.prepare and LATEST.is_file():
        try:
            cand = json.loads(LATEST.read_text(encoding="utf-8"))
            ts = datetime.strptime(cand["generated_at"], "%Y-%m-%d %H:%M:%S")
            if abs((datetime.now() - ts).total_seconds()) < 7200:
                meta = cand
        except (json.JSONDecodeError, KeyError, ValueError):
            meta = None
    if meta is None:
        meta = prepare()
    if args.send_smtp:
        send_smtp(meta)
        mark_sent()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
