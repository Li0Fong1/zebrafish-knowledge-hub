# -*- coding: utf-8 -*-
"""
导出「未达金标」3824 篇的可操作下载指引（服务器跑）。

一眼能懂：动作分类 + 分步操作 + 全部直达链接 + 本地路径 + 建议保存名。

输出（默认 RUNTIME/logs/scheduler/，同时写解析文件/ 便于本机拉取）：
  non_gold_download_guide_latest.csv
  non_gold_download_guide_latest.xlsx   （有 openpyxl 时）
  non_gold_download_guide_summary.json
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RUNTIME = Path(os.environ.get("ZEBRA_RUNTIME", "/data/zoujin/deng/zebrafish_runtime"))
BASE = Path(os.environ.get("ZEBRA_BASE", str(ROOT)))
LOG_DIR = RUNTIME / "logs" / "scheduler"
NC_PARSE = LOG_DIR / "non_compliant_parse.jsonl"
OUT_DIR = Path(os.environ.get("NON_GOLD_GUIDE_OUT", str(LOG_DIR)))
LOCAL_MIRROR = ROOT / "解析文件"
FLOOR = 35487
INBOX_SAVE = RUNTIME / "gold_ingest" / "pdfku_inbox" / "scihub_batch"

HEADERS = [
    "entry_no",
    "标题",
    "DOI",
    "PMID",
    "PMCID",
    "正文长度",
    "金标门槛",
    "距门槛差",
    "体量档位",
    "审计问题",
    "台账原因",
    "已有本地PDF",
    "本地PDF路径",
    "PDF大小MB",
    "动作分类",
    "第一步",
    "第二步",
    "第三步",
    "DOI页面",
    "SciHub页面",
    "PubMed页面",
    "PMC页面",
    "OA直链",
    "建议保存为",
    "保存到目录",
    "优先级",
    "备注",
]


def _safe_doi_fname(doi: str) -> str:
    return (doi or "").strip().lower().replace("/", "_").replace(":", "_")


def _tier(body: int) -> str:
    if body >= 30000:
        return "近门槛(≥3万)"
    if body >= 20000:
        return "中等(2–3万)"
    if body >= 5000:
        return "远短(5k–2万)"
    if body > 0:
        return "极短(<5k)"
    return "无正文"


def _links(doi: str, pmid: str, pmcid: str) -> dict[str, str]:
    doi = (doi or "").strip()
    pmid = (pmid or "").strip()
    pmcid = (pmcid or "").strip()
    out = {
        "doi_page": f"https://doi.org/{doi}" if doi else "",
        "scihub": f"https://sci-hub.se/{doi}" if doi else (f"https://sci-hub.se/{pmid}" if pmid else ""),
        "pubmed": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid.isdigit() else "",
        "pmc": f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/" if pmcid else "",
    }
    return out


def _classify(
    *,
    body: int,
    gap: int,
    has_pdf: bool,
    pdf_mb: float,
    doi: str,
    pmid: str,
    pmcid: str,
    unreach: str,
    strict: list[str],
    issue: str,
    oa_url: str,
) -> tuple[str, str, str, str, str, str]:
    """返回 (动作分类, 第一步, 第二步, 第三步, 优先级, 备注)。"""
    ur = (unreach or "").lower()
    st = ";".join(str(x) for x in (strict or [])[:5])
    links = _links(doi, pmid, pmcid)
    save_hint = INBOX_SAVE.as_posix()

    if oa_url:
        return (
            "OA直链下载",
            f"浏览器打开 OA直链 列（或 {oa_url[:80]}…）",
            "下载 PDF，核对页数≥10且非仅摘要",
            f"重命名为「建议保存为」列文件名，上传到服务器 {save_hint}",
            "高",
            "开放获取，优先此路",
        )

    if pmcid and not has_pdf:
        return (
            "PMC免费全文",
            f"打开 PMC页面：{links['pmc']}",
            "点 PDF 下载（或 Europe PMC 全文）",
            f"保存为建议文件名，上传到 {save_hint}",
            "高",
            "",
        )

    if body >= FLOOR and st:
        return (
            "正文够长·质量未过",
            "现有 PDF 抽取质量不合格（strict/score/order）",
            f"换出版社原版或更高清扫描 PDF（DOI：{doi or '见PubMed'}）",
            f"SciHub 或机构库重下 → 上传 {save_hint} → 通知重跑 repair",
            "中",
            f"strict: {st[:120]}",
        )

    if gap > 0 and gap <= 8000 and body >= 28000:
        return (
            "近门槛·换完整PDF",
            f"当前正文 {body}，差金标 {gap} 字——多为缺页/乱码/仅主文缺附录",
            f"用 DOI 在 SciHub 或出版社站换「完整版」PDF（{links['scihub'] or links['doi_page']}）",
            f"覆盖上传至 {save_hint}（勿删 entry_no 前缀）",
            "高",
            "换源比 OCR 优先",
        )

    if has_pdf and body < 20000:
        return (
            "本地PDF不完整",
            f"服务器已有 PDF（{pdf_mb:.1f}MB）但正文仅 {body} 字",
            f"重新下载全文 PDF：SciHub {links['scihub'] or '无DOI则PubMed搜标题'}",
            f"新 PDF 命名为建议保存名，上传到 {save_hint}",
            "高" if doi else "中",
            "勿重复用同一残缺文件",
        )

    if doi and (not has_pdf or gap > 8000):
        return (
            "SciHub/DOI重下全文",
            f"打开 SciHub：{links['scihub']}",
            f"若无 SciHub，试 DOI 页 {links['doi_page']} 或机构 VPN",
            f"下载后按「建议保存为」上传至 {save_hint}",
            "高",
            "",
        )

    if pmid and not doi:
        return (
            "PubMed查DOI再下",
            f"打开 PubMed：{links['pubmed']}",
            "复制 DOI / 点 Full text links 找 PDF",
            f"拿到 PDF 后上传 {save_hint}，文件名带 entry_no 前缀",
            "中",
            "",
        )

    if "online_exhausted" in ur or "online_exhausted" in issue:
        return (
            "线上已穷尽·机构访问",
            "自动 OA/SciHub/EPMC 均已试过",
            "用学校/单位 VPN 或馆际互借下载出版社 PDF",
            f"人工 PDF 上传至 {save_hint} 后标记 entry_no",
            "低",
            ur or issue,
        )

    if body < 5000:
        return (
            "极短稿·先确认有无全文",
            "正文极短，可能是书籍章节/摘要/抽取失败",
            "PubMed/Google Scholar 确认该 entry 是否真有期刊全文",
            "若确无全文则保持台账，不必强行下载",
            "低",
            "避免浪费时间下摘要",
        )

    if not doi and not pmid:
        return (
            "无DOI/PMID·标题检索",
            "在 PubMed 或 Google Scholar 用「标题」列检索",
            "找到 DOI 后按 SciHub/DOI 流程下载",
            f"PDF 上传 {save_hint}",
            "低",
            "",
        )

    return (
        "待人工判断",
        f"查看 DOI/PubMed 链接；issue={issue}",
        "按缺稿原因选 OA / SciHub / 机构库",
        f"上传至 {save_hint}",
        "中",
        ur or st or issue,
    )


def _row_from_eno(
    eno: str,
    nc: dict[str, Any],
    *,
    rec: dict[str, Any] | None,
    log_row: dict[str, Any],
    unreach: dict[str, Any],
    pdf: Path | None,
    oa: dict[str, Any],
) -> dict[str, str]:
    body = int(nc.get("body_len") or 0)
    if rec:
        body = max(body, len(rec.get("body_text") or ""))
    gap = max(0, FLOOR - body) if body < FLOOR else 0
    doi = str((rec or {}).get("doi") or log_row.get("doi") or unreach.get("doi") or "").strip()
    if not doi and pdf:
        parts = pdf.stem.split("_", 1)
        if len(parts) == 2 and parts[1].startswith("10."):
            doi = parts[1].replace("_", "/")
    pmid = str((rec or {}).get("pmid") or log_row.get("pmid") or unreach.get("pmid") or "").strip()
    pmcid = str((rec or {}).get("pmcid") or log_row.get("pmcid") or unreach.get("pmcid") or "").strip()
    title = str(
        (rec or {}).get("title")
        or log_row.get("en_title")
        or log_row.get("title")
        or unreach.get("title")
        or ""
    ).strip()[:300]
    issue = str(nc.get("issue") or "")
    strict = nc.get("strict_issues") or (rec or {}).get("strict_issues") or []
    if isinstance(strict, str):
        strict = [strict] if strict else []
    unreach_reason = str(unreach.get("reason") or unreach.get("reason_bucket") or "")
    has_pdf = bool(pdf and pdf.is_file())
    pdf_mb = (pdf.stat().st_size / (1024 * 1024)) if has_pdf else 0.0
    oa_urls = (oa.get("pdf_urls") or []) if oa else []
    oa_url = str(oa_urls[0]) if oa_urls else ""

    action, s1, s2, s3, pri, note = _classify(
        body=body,
        gap=gap,
        has_pdf=has_pdf,
        pdf_mb=pdf_mb,
        doi=doi,
        pmid=pmid,
        pmcid=pmcid,
        unreach=unreach_reason,
        strict=list(strict)[:8],
        issue=issue,
        oa_url=oa_url,
    )
    links = _links(doi, pmid, pmcid)
    fname = f"{eno}_{_safe_doi_fname(doi)}.pdf" if doi else f"{eno}.pdf"
    audit_bits = [issue] if issue else []
    if strict:
        audit_bits.append("strict:" + ";".join(str(x) for x in strict[:5]))
    if nc.get("log_status"):
        audit_bits.append(f"log={nc['log_status']}")

    return {
        "entry_no": eno,
        "标题": title,
        "DOI": doi,
        "PMID": pmid,
        "PMCID": pmcid,
        "正文长度": str(body),
        "金标门槛": str(FLOOR),
        "距门槛差": str(gap),
        "体量档位": _tier(body),
        "审计问题": " | ".join(audit_bits)[:200],
        "台账原因": unreach_reason[:120],
        "已有本地PDF": "是" if has_pdf else "否",
        "本地PDF路径": str(pdf) if has_pdf else str(nc.get("pdf") or log_row.get("pdf") or ""),
        "PDF大小MB": f"{pdf_mb:.2f}" if has_pdf else "",
        "动作分类": action,
        "第一步": s1,
        "第二步": s2,
        "第三步": s3,
        "DOI页面": links["doi_page"],
        "SciHub页面": links["scihub"],
        "PubMed页面": links["pubmed"],
        "PMC页面": links["pmc"],
        "OA直链": oa_url,
        "建议保存为": fname,
        "保存到目录": str(INBOX_SAVE),
        "优先级": pri,
        "备注": note,
    }


def _load_nc_parse() -> list[dict[str, Any]]:
    if not NC_PARSE.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with NC_PARSE.open(encoding="utf-8", errors="replace") as fh:
        for ln in fh:
            ln = ln.strip()
            if not ln:
                continue
            try:
                rows.append(json.loads(ln))
            except Exception:
                continue
    return rows


def _load_unreachable() -> dict[str, dict[str, Any]]:
    from scripts.scihub_post_gold_pipeline import UNREACHABLE

    out: dict[str, dict[str, Any]] = {}
    if not UNREACHABLE.is_file():
        return out
    with UNREACHABLE.open(encoding="utf-8", errors="replace") as fh:
        for ln in fh:
            try:
                o = json.loads(ln)
            except Exception:
                continue
            rid = str(o.get("id") or o.get("entry_no") or "").strip()
            if rid:
                out[rid] = o
    return out


def _write_xlsx(path: Path, rows: list[dict[str, str]]) -> bool:
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font
        from openpyxl.utils import get_column_letter
    except ImportError:
        return False
    wb = Workbook()
    ws = wb.active
    ws.title = "未达金标下载指引"
    ws.append(HEADERS)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for r in rows:
        ws.append([r.get(h, "") for h in HEADERS])
    for col_idx, h in enumerate(HEADERS, 1):
        width = 14
        if h in ("标题", "第一步", "第二步", "第三步", "本地PDF路径", "备注", "审计问题"):
            width = 36
        elif h in ("DOI页面", "SciHub页面", "OA直链"):
            width = 28
        ws.column_dimensions[get_column_letter(col_idx)].width = width
    for row in ws.iter_rows(min_row=2, max_row=ws.max_row):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    wb.save(path)
    return True


def export(*, regen_audit: bool = False) -> dict[str, Any]:
    os.environ.setdefault("ZEBRA_RUNTIME", str(RUNTIME))
    os.environ.setdefault("ZEBRA_BASE", str(BASE))
    os.chdir(BASE)
    if str(BASE) not in sys.path:
        sys.path.insert(0, str(BASE))

    from core.entry import bootstrap_app

    bootstrap_app()

    if regen_audit or not NC_PARSE.is_file():
        from scripts.full_compliance_audit import run_audit

        run_audit()

    nc_rows = _load_nc_parse()
    if not nc_rows:
        raise SystemExit(f"no rows in {NC_PARSE}")

    from scripts.gold_hunt_fulltext import load_oa_disk, normalize_doi
    from scripts.scihub_post_gold_pipeline import find_inbox_pdf, load_rec, parse_log_rows

    log_by = parse_log_rows()
    un_by = _load_unreachable()
    oa_disk = load_oa_disk()

    out_rows: list[dict[str, str]] = []
    for nc in nc_rows:
        eno = str(nc.get("entry_no") or "").strip()
        if not eno:
            continue
        rec = load_rec(eno)
        pdf = find_inbox_pdf(eno)
        if not pdf and nc.get("pdf"):
            p = Path(str(nc["pdf"]))
            if p.is_file():
                pdf = p
        doi_n = normalize_doi(str((rec or {}).get("doi") or log_by.get(eno, {}).get("doi") or ""))
        oa = oa_disk.get(doi_n, {}) if doi_n else {}
        out_rows.append(
            _row_from_eno(
                eno,
                nc,
                rec=rec,
                log_row=log_by.get(eno, {}),
                unreach=un_by.get(eno, {}),
                pdf=pdf,
                oa=oa,
            )
        )

    out_rows.sort(key=lambda r: ({"高": 0, "中": 1, "低": 2}.get(r["优先级"], 9), -int(r["距门槛差"] or 0)))

    ts = datetime.now().strftime("%Y%m%d_%H%M")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    LOCAL_MIRROR.mkdir(parents=True, exist_ok=True)

    csv_ts = OUT_DIR / f"non_gold_download_guide_{ts}.csv"
    csv_latest = OUT_DIR / "non_gold_download_guide_latest.csv"
    xlsx_latest = OUT_DIR / "non_gold_download_guide_latest.xlsx"
    summary_path = OUT_DIR / "non_gold_download_guide_summary.json"

    mirror_csv = LOCAL_MIRROR / "non_gold_download_guide_latest.csv"
    mirror_xlsx = LOCAL_MIRROR / "non_gold_download_guide_latest.xlsx"
    mirror_summary = LOCAL_MIRROR / "non_gold_download_guide_summary.json"

    for p in (csv_ts, csv_latest, mirror_csv):
        with p.open("w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=HEADERS)
            w.writeheader()
            w.writerows(out_rows)

    has_xlsx = _write_xlsx(xlsx_latest, out_rows)
    if has_xlsx:
        import shutil

        shutil.copy2(xlsx_latest, mirror_xlsx)

    by_action = Counter(r["动作分类"] for r in out_rows)
    by_pri = Counter(r["优先级"] for r in out_rows)
    by_tier = Counter(r["体量档位"] for r in out_rows)
    high_dl = [r for r in out_rows if r["优先级"] == "高"]

    summary = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "source": str(NC_PARSE),
        "total": len(out_rows),
        "floor": FLOOR,
        "by_action": dict(by_action.most_common()),
        "by_priority": dict(by_pri),
        "by_tier": dict(by_tier),
        "high_priority_count": len(high_dl),
        "inbox_upload_dir": str(INBOX_SAVE),
        "files": {
            "csv_latest": str(csv_latest),
            "csv_timestamped": str(csv_ts),
            "xlsx_latest": str(xlsx_latest) if has_xlsx else "",
            "summary": str(summary_path),
        },
        "usage": "按「优先级=高」筛选；看「动作分类+第一/二/三步」即可操作；PDF 上传到保存到目录",
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    mirror_summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--regen-audit", action="store_true", help="先重跑 full_compliance_audit")
    args = ap.parse_args()
    export(regen_audit=args.regen_audit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
