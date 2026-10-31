# -*- coding: utf-8 -*-
"""生成五篇测试可读文本，并尝试发到微信「文件传输助手」。"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JSON_PATH = ROOT / "解析文件" / "向量化_五篇直观测试结果.json"
TXT_PATH = ROOT / "解析文件" / "向量化_五篇直观测试结果_发给微信.txt"


def build_text() -> str:
    d = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    if d.get("hard_fail"):
        raise RuntimeError(f"硬门禁失败，禁止发送: {d['hard_fail']}")
    cases = d.get("cases") or []
    if len(cases) < 5:
        raise RuntimeError(f"样例不足 5: {len(cases)}")
    for i, c in enumerate(cases[:5], 1):
        if c.get("rank") != 1 or str(c.get("top1_pmid")) != str(c.get("pmid")):
            raise RuntimeError(f"样例{i} 非第1命中，禁止发送")
    if (d.get("hub") or {}).get("source") != "pubmed_yes":
        raise RuntimeError("Hub 未走 pubmed_yes，禁止发送")

    lines = [
        "斑马鱼文献向量化 · 五篇直观测试结果",
        "时间：" + datetime.now().strftime("%Y-%m-%d %H:%M"),
        f"索引篇数：{d.get('index_papers')} | 验收：5/5 第1命中 | Hub：pubmed_yes",
        "说明：score 为距离，越小越相似",
        "",
    ]
    for i, c in enumerate(cases[:5], 1):
        lines.append(f"【测试{i}】PMID {c['pmid']} · 自检索第{c['rank']}名")
        lines.append(f"中文：{c.get('title_zh') or '(无)'}")
        lines.append(f"英文：{(c.get('title_en') or '')[:120]}")
        lines.append(f"提问：{(c.get('query') or '')[:140]}")
        lines.append("Top3：")
        for h in c.get("hits") or []:
            mark = " ←目标" if str(h.get("pmid")) == str(c["pmid"]) else ""
            lines.append(
                f"  #{h['rank']} PMID={h.get('pmid')} score={h.get('score')} "
                f"{h.get('title_zh') or ''}{mark}"
            )
            sn = (h.get("snippet") or "")[:80]
            lines.append(f"     片段：{sn}…")
        lines.append("")
    lines.append("门禁：全部第1命中 / 含PMID与中文标题 / Hub走pubmed_yes")
    text = "\n".join(lines)
    TXT_PATH.parent.mkdir(parents=True, exist_ok=True)
    TXT_PATH.write_text(text, encoding="utf-8")
    return text


def send_wechat_file_helper(text: str) -> None:
    """优先 wxauto，其次 uiautomation。"""
    # 1) wxauto
    try:
        from wxauto import WeChat  # type: ignore

        wx = WeChat()
        who = "文件传输助手"
        wx.ChatWith(who)
        time.sleep(0.8)
        # 分段发送，避免过长
        chunks = []
        buf = []
        n = 0
        for line in text.splitlines():
            if n + len(line) + 1 > 1800 and buf:
                chunks.append("\n".join(buf))
                buf = [line]
                n = len(line)
            else:
                buf.append(line)
                n += len(line) + 1
        if buf:
            chunks.append("\n".join(buf))
        for i, ch in enumerate(chunks, 1):
            wx.SendMsg(ch, who=who)
            time.sleep(0.5)
            print(f"[wxauto] sent chunk {i}/{len(chunks)}")
        # 再发文件
        wx.SendFiles(str(TXT_PATH), who=who)
        print("[wxauto] sent file OK")
        return
    except Exception as e:
        print(f"[wxauto] fail: {e}")

    # 2) uiautomation 简易：打开搜索 → 文件传输助手 → 粘贴发送
    try:
        import uiautomation as auto  # type: ignore
        import pyperclip  # type: ignore

        # 找微信主窗口
        win = None
        for name in ("微信", "Weixin", "WeChat"):
            win = auto.WindowControl(searchDepth=1, Name=name)
            if win.Exists(0, 0):
                break
        if not win or not win.Exists(0, 0):
            raise RuntimeError("未找到微信主窗口，请先打开并登录 PC 微信")
        win.SetActive()
        time.sleep(0.3)
        # Ctrl+F 搜索
        auto.SendKeys("{Ctrl}f")
        time.sleep(0.4)
        auto.SendKeys("{Ctrl}a")
        auto.SendKeys("文件传输助手")
        time.sleep(0.6)
        auto.SendKeys("{Enter}")
        time.sleep(0.8)
        pyperclip.copy(text)
        auto.SendKeys("{Ctrl}v")
        time.sleep(0.3)
        auto.SendKeys("{Enter}")
        print("[uiautomation] text sent OK")
        return
    except Exception as e:
        print(f"[uiautomation] fail: {e}")
        raise RuntimeError(
            "无法自动发送到微信文件传输助手。请确认 PC 微信已登录前台，"
            "或安装: pip install wxauto uiautomation pyperclip"
        ) from e


def main() -> int:
    text = build_text()
    print("TEXT_READY", TXT_PATH, "chars", len(text))
    send_wechat_file_helper(text)
    print("SEND_DONE")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        print("SEND_FAIL:", e, file=sys.stderr)
        raise SystemExit(1)
