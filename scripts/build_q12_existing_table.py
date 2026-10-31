# -*- coding: utf-8 -*-
"""
将 是_Q12.md 与 online_fulltext 命中库求交，写出「已存在表」。
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(r"D:\PycharmProjects\斑马鱼基因问答体")
Q12 = ROOT / "解析文件" / "是_Q12.md"
OUT_DIR = ROOT / "解析文件"
OUT_MD = OUT_DIR / "已存在表.md"
OUT_CSV = OUT_DIR / "已存在表.csv"
LOG = OUT_DIR / "已存在表_run.log"
HIT_DIR = ROOT / "zebrafish_package" / "结果" / "online_fulltext"


def log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def norm_title(t: str) -> str:
    t = (t or "").lower().strip()
    t = t.replace("’", "'").replace("“", '"').replace("”", '"')
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def parse_q12(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8")
    entries: list[dict] = []
    # ### N. title
    blocks = re.split(r"\n(?=###\s+\d+\.)", text)
    for block in blocks:
        m = re.match(r"###\s+(\d+)\.\s*(.+)", block.strip())
        if not m:
            continue
        q12_no = m.group(1)
        heading = m.group(2).strip()
        fields: dict[str, str] = {}
        for fm in re.finditer(r"\|\s*([^|]+?)\s*\|\s*([^|]*?)\s*\|", block):
            k = fm.group(1).strip()
            v = fm.group(2).strip()
            if k.lower() == "field" or k == "------":
                continue
            fields[k] = v
        en = fields.get("EN Title") or heading
        entries.append(
            {
                "q12_no": q12_no,
                "en_title": en,
                "zh_title": fields.get("ZH Title") or "",
                "journal": fields.get("Journal") or "",
                "issn": fields.get("ISSN") or "",
                "an": fields.get("AN") or "",
                "judgment": fields.get("Judgment") or "",
                "norm": norm_title(en),
            }
        )
    return entries


def load_hits() -> dict[str, dict]:
    """norm_title -> best hit record."""
    by_title: dict[str, dict] = {}
    files = list(HIT_DIR.glob("results_w*.jsonl"))
    merged = HIT_DIR / "results.jsonl"
    if merged.is_file():
        files.append(merged)
    # also sectioned if present
    sec = HIT_DIR / "results_sectioned.jsonl"
    if sec.is_file():
        files.append(sec)

    seen_files = []
    for path in files:
        if not path.is_file() or path in seen_files:
            continue
        seen_files.append(path)
        for line in path.open(encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("status") and r.get("status") != "ok":
                continue
            title = r.get("en_title") or r.get("title") or ""
            key = norm_title(title)
            if not key:
                continue
            chars = int(r.get("text_chars") or 0)
            old = by_title.get(key)
            if old is None or chars >= int(old.get("text_chars") or 0):
                by_title[key] = {
                    "entry_no": str(r.get("entry_no") or ""),
                    "en_title": title,
                    "doi": r.get("doi") or "",
                    "pmid": r.get("pmid") or "",
                    "pmcid": r.get("pmcid") or "",
                    "content_type": r.get("content_type") or "",
                    "source_step": r.get("source_step") or "",
                    "text_chars": chars,
                    "source_url": r.get("source_url") or "",
                }
    return by_title


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if LOG.exists():
        LOG.write_text("", encoding="utf-8")
    t0 = time.time()
    log(f"读取 {Q12}")
    q12 = parse_q12(Q12)
    log(f"是_Q12 条目数: {len(q12)}")
    log("加载全文命中库…")
    hits = load_hits()
    log(f"命中库唯一标题数: {len(hits)}")

    rows = []
    for e in q12:
        h = hits.get(e["norm"])
        if not h:
            continue
        rows.append({**e, **h})

    log(f"同时存在: {len(rows)}")

    # markdown table
    md = [
        "# 已存在表",
        "",
        f"- **生成时间**：{datetime.now().isoformat(timespec='seconds')}",
        f"- **是_Q12.md 条目数**：{len(q12)}",
        f"- **全文命中库标题数**：{len(hits)}",
        f"- **同时存在（按英文标题对齐）**：{len(rows)}",
        f"- **数据来源**：`解析文件/是_Q12.md` ∩ `zebrafish_package/结果/online_fulltext/results*.jsonl`",
        "",
        "| Q12序号 | 库entry_no | 英文标题 | 中文标题 | 期刊 | DOI | 类型 | 来源 | 正文字符数 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        en = (r.get("en_title") or "").replace("|", "/")
        zh = (r.get("zh_title") or "").replace("|", "/")
        if len(en) > 80:
            en = en[:77] + "..."
        if len(zh) > 40:
            zh = zh[:37] + "..."
        md.append(
            f"| {r.get('q12_no')} | {r.get('entry_no')} | {en} | {zh} | "
            f"{(r.get('journal') or '')[:30]} | {r.get('doi') or '—'} | "
            f"{r.get('content_type') or '—'} | {r.get('source_step') or '—'} | {r.get('text_chars') or 0} |"
        )
    md.append("")
    OUT_MD.write_text("\n".join(md), encoding="utf-8")

    # csv
    import csv

    with OUT_CSV.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(
            fh,
            fieldnames=[
                "q12_no",
                "entry_no",
                "en_title",
                "zh_title",
                "journal",
                "an",
                "doi",
                "pmid",
                "pmcid",
                "content_type",
                "source_step",
                "text_chars",
                "source_url",
            ],
        )
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in w.fieldnames})

    log(f"已写入 {OUT_MD}")
    log(f"已写入 {OUT_CSV}")
    log(f"完成，耗时 {time.time()-t0:.1f}s，共 {len(rows)} 行")


if __name__ == "__main__":
    main()
