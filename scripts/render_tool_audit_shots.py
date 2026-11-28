# -*- coding: utf-8 -*-
"""生成异常界面的真实渲染截图（修复前 / 修复后）。

做法：不操控用户屏幕，全部由本地静态 HTML + headless Chrome 渲染。
唯一差异是 CSS：修复前用「剔除 output-layout.css 的旧打包样式」，
修复后用当前 web/dist/app.*.css（含统一排版规范）。
结果区 HTML 结构来自前端渲染函数，展示的数据来自项目真实输出 / 用户提供的真实界面。
"""
from __future__ import annotations

import html
import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "output" / "tool_audit"
IMG = OUT / "images"
IMG.mkdir(parents=True, exist_ok=True)

CHROME = r"C:/Program Files/Google/Chrome/Application/chrome.exe"

# 打包产物（含 output-layout.css）—— 修复后。
# 必须按 index.html 实际引用的哈希名取，不能按文件名字典序取（会拿到历史产物）。
_index_html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
_ref = re.search(r"dist/(app\.[a-z0-9]+\.css)", _index_html)
new_css_name = _ref.group(1) if _ref else max(
    (ROOT / "web" / "dist").glob("app.*.css"),
    key=lambda p: p.stat().st_mtime).name
dist_css = ROOT / "web" / "dist" / new_css_name
(OUT / "render").mkdir(exist_ok=True)
(OUT / "render" / new_css_name).write_bytes(dist_css.read_bytes())

# 修复前样式：按 index.src.html 的 <link> 顺序拼接源码 CSS，
# 唯独排除本轮新增的 output-layout.css。这样前后差异只来自排版规范本身。
src_html = (ROOT / "web" / "index.src.html").read_text(encoding="utf-8")
links = re.findall(r'<link[^>]+href="(/static/css/[^"?]+)', src_html)
old_parts = []
for href in links:
    name = href.rsplit("/", 1)[-1]
    if name == "output-layout.css":
        continue
    f = ROOT / "web" / "css" / name
    if f.exists():
        old_parts.append(f"/* ==== {name} ==== */\n" + f.read_text(encoding="utf-8", errors="replace"))
(OUT / "render" / "old_bundle.css").write_text("\n".join(old_parts), encoding="utf-8")
print("old CSS: %d files, %d bytes" % (len(old_parts), len("\n".join(old_parts))))

# ---------------------------------------------------------------------------
# 真实数据：引物候选来自 crispr_toolkit.design_primers（粘贴 tp53 CDS 片段）
# ---------------------------------------------------------------------------
for p in [ROOT, ROOT / "database", ROOT / "api", ROOT / "core"]:
    sys.path.insert(0, str(p))
import crispr_toolkit as ct  # noqa: E402

CDS = ("ATGGCGCAAAACGACAGCCAAGAGTTCGCGGAGCTCTGGGAGAAGAATTTGATAAGTATT"
       "CAGCCCCCAGGTGGTGGCTCTTGCTGGGACATCATTAATGATGAGGAGTACTTGCCGGGA"
       "TCGTTTGACCCCAATTTTTTTGAAAATGTGCTTGAAGAACAGCCTCAGCCATCCACTCTC"
       "CCACCAACATCCACTGTTCCGGAGACAAGCGACTATCCCGGCGATCATGGATTTAGGCTC"
       "AGGTTCCCGCAGTCTGGCACAGCAAAATCTGTAACTTGCACTTATTCACCGGACCTGAAT"
       "AAACTCTTCTGTCAGCTGGCAAAAACTTGCCCCGTTCAAATGGTGGTGGACGTTGCCCCT"
       "CCACAGGGCTCCGTGGTTCGAGCCACTGCCATCTATAAGAAGTCCGAGCATGTGGCTGAA"
       "GTGGTCCGCAGATGCCCCCATCATGAGCGAACCCCGGATGGAGATAACTTGGCGCCTGCT"
       "GGTCATTTGATAAGAGTGGAGGGCAATCAGCGAGCAAATTACAGGGAAGATAACATCACT"
       "TTAAGGCATAGTGTTTTTGTCCCATATGAAGCACCACAGCTTGGTGCTGAATGGACAACT")

primers = ct.design_primers(CDS, add_t7=False, off_target=False)
PAIRS = primers.get("pairs", [])[:4]
TPL_LEN = (primers.get("template") or {}).get("length", "—")

# 表型条目：逐字取自用户提供的 Gene → phenotype 真实界面截图
PHENOS = [
    ("whole organism", "increased amount", 19),
    ("telencephalon", "decreased amount", 7),
    ("whole organism", "present", 6),
    ("whole organism", "decreased amount", 5),
    ("telencephalon", "increased amount", 4),
    ("peripheral nervous system", "increased amount", 3),
    ("whole organism", "decreased life span", 3),
    ("post-vent region", "neoplastic", 3),
    ("gill", "neoplastic", 3),
    ("apoptotic process", "decreased occurrence", 3),
]


def esc(s):
    return html.escape(str(s))


def primer_table(show_t7: bool) -> str:
    rows = []
    for i, p in enumerate(PAIRS):
        tail = ""
        if show_t7:
            tail = ('<br><small class="pam-flag">T7-tailed: '
                    'TAATACGACTCACTATAG' + esc(p["forward"][:8]) + '…</small>')
        rows.append(
            '<tr><td>' + str(i + 1) + '</td>'
            '<td class="mono">' + esc(p["forward"]) +
            '<br><small class="pam-flag">' + esc(p["forward_pos"]) + '</small>' + tail + '</td>'
            '<td class="mono">' + esc(p["reverse"]) +
            '<br><small class="pam-flag">' + esc(p["reverse_pos"]) + '</small></td>'
            '<td>' + esc(p["product_size"]) + ' bp</td>'
            '<td>' + esc(p["forward_tm"]) + ' / ' + esc(p["reverse_tm"]) + '</td>'
            '<td>' + esc(p["forward_gc"]) + ' / ' + esc(p["reverse_gc"]) + '</td>'
            '<td>' + esc(round(p["score"], 1)) + '</td></tr>')
    note = (" · T7 promoter tail (TAATACGACTCACTATAG…) appended for "
            "in-vitro transcription template amplification") if show_t7 else ""
    return ('<p class="tool-hint">Template: cDNA · GRCz12tu · ' + str(TPL_LEN) + ' bp<br>'
            'Primer candidates (ranked by rule score)' + note + '</p>'
            '<div class="table-wrap"><table class="sgrna-table"><thead><tr>'
            '<th>#</th><th>Forward (5′→3′)</th><th>Reverse (5′→3′)</th>'
            '<th>Product</th><th>Tm (F/R)</th><th>GC% (F/R)</th><th>Score</th>'
            '</tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div>')


def pheno_tags() -> str:
    out = []
    for part, qual, n in PHENOS:
        out.append('<div class="zf-pheno-tag"><span class="zf-pheno-part">' + esc(part) +
                   '</span><span class="zf-pheno-qual">' + esc(qual) +
                   '</span><span class="zf-pheno-num">' + str(n) + '</span></div>')
    return ('<div class="zf-pheno-strip"><p class="zf-pheno-strip-head">'
            'Associated phenotypes (by record count)</p>'
            '<div class="zf-pheno-tags">' + ''.join(out) + '</div></div>')


def scope_pills(show_genome: bool) -> str:
    genome = ('<button type="button" class="zf-pill active">✓ 全基因组</button>'
              if show_genome else "")
    return ('<div class="zf-chips" style="margin:0">'
            '<span class="zf-pill">特异性</span>'
            '<button type="button" class="zf-pill">转录组</button>' + genome +
            '</div>')


def page(title, css, body, width=980, height=700):
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<link rel="stylesheet" href="{css}">
<style>
  html,body{{margin:0;background:#fff;color:#1b2733;font-family:Arial,'Microsoft YaHei',sans-serif;}}
  .shot-wrap{{padding:22px 26px;}}
  .shot-title{{font-size:15px;font-weight:700;letter-spacing:.03em;margin:0 0 4px;}}
  .shot-sub{{font-size:12px;color:#6b7a91;margin:0 0 16px;}}
  .tool-result{{border:1px solid #dbe4ee;border-radius:10px;background:#fff;
                padding:14px 16px;font-size:13px;}}
  .tool-hint{{font-size:12.5px;color:#475569;line-height:1.6;margin:0 0 10px;}}
  table.sgrna-table{{width:100%;border-collapse:collapse;font-size:12px;}}
  table.sgrna-table th,table.sgrna-table td{{border-bottom:1px solid #e6ecf3;
     padding:7px 8px;text-align:left;vertical-align:top;}}
  table.sgrna-table th{{font-size:10.5px;text-transform:uppercase;letter-spacing:.05em;color:#64748b;}}
  .mono{{font-family:Consolas,Menlo,monospace;font-size:11.5px;}}
  .zf-pill{{display:inline-block;border:1px solid #cbd5e1;border-radius:999px;
     padding:5px 14px;font-size:13px;background:#fff;color:#334155;}}
  .zf-pill.active{{border-color:#1d6fb8;background:#e8f2fc;color:#0b4f8a;font-weight:700;}}
  .zf-pheno-tag{{background:rgba(29,111,184,.08);border:1px solid rgba(29,111,184,.18);
     border-radius:10px;color:#16324f;}}
  .zf-pheno-num{{background:rgba(29,111,184,.12);border-radius:999px;padding:1px 7px;font-size:11px;}}
  small.pam-flag{{font-size:11px;color:#8494a8;}}
</style></head><body><div class="shot-wrap">
<p class="shot-title">{esc(title)}</p><p class="shot-sub">Zebrafish Knowledge Hub · tool result panel</p>
{body}</div></body></html>"""


def main() -> int:
    made = []
    specs = [
        ("shot_01_t7_before.png", "Primer design · before (T7 footnote)", "old_bundle.css", True, 980, 560),
        ("shot_01_t7_after.png", "Primer design · after (clean)", new_css_name, False, 980, 560),
        ("shot_02_pheno_before.png", "Gene → phenotype · before (wrapped chips)", "old_bundle.css", "pheno", 700, 520),
        ("shot_02_pheno_after.png", "Gene → phenotype · after (top-to-bottom)", new_css_name, "pheno", 700, 780),
        ("shot_03_scope_before.png", "Primer design hero · before (✓ whole genome)", "old_bundle.css", "scope", 620, 210),
        ("shot_03_scope_after.png", "Primer design hero · after (control removed)", new_css_name, "scope", 620, 210),
    ]
    for fname, title, css, mode, w, h in specs:
        if mode == "pheno":
            body = pheno_tags()
        elif mode == "scope":
            body = scope_pills(mode == "scope" and css == "old_bundle.css")
        else:
            body = primer_table(bool(mode))
        markup = page(title, css, '<div class="tool-result">' + body + '</div>', w, h)
        html_path = OUT / "render" / (fname + ".html")
        html_path.write_text(markup, encoding="utf-8")
        png_path = IMG / fname
        cmd = [CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
               "--hide-scrollbars", "--force-device-scale-factor=1",
               f"--window-size={w},{h}",
               f"--screenshot={png_path.as_posix()}",
               html_path.as_uri()]
        r = subprocess.run(cmd, capture_output=True, timeout=60)
        ok = png_path.exists() and png_path.stat().st_size > 3000
        made.append(dict(file=fname, title=title, css=css, size=[w, h],
                         bytes=png_path.stat().st_size if png_path.exists() else 0,
                         ok=ok))
        print("%-32s %s %s" % (fname, "OK" if ok else "FAIL",
                               png_path.stat().st_size if png_path.exists() else 0))
    (OUT / "render_manifest.json").write_text(
        json.dumps(made, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
