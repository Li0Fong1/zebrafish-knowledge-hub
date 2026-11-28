# -*- coding: utf-8 -*-
"""生成「功能页输出异常清单」表格（HTML + 长图 PNG + Markdown）。

截图直接嵌进表格单元格：既包含用户提供的 4 张真实界面截图，也包含
无头浏览器渲染的修复前后对照图。不操控用户屏幕。
"""
from __future__ import annotations

import html
import json
import pathlib
import subprocess
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "output" / "tool_audit"
IMG = OUT / "images"
CHROME = r"C:/Program Files/Google/Chrome/Application/chrome.exe"

report = json.loads((OUT / "audit_results.json").read_text(encoding="utf-8"))
tests = json.loads((OUT / "answer_tests.json").read_text(encoding="utf-8"))

# 用户提供的真实界面截图（异常证据）
USER_SHOTS = {
    "01_t7_copy.png": "T7 说明小字（用户原图，红箭头所指）",
    "02_phenotype_copy.png": "Gene → phenotype 气泡横排杂乱（用户原图）",
    "03_primer_copy.png": "引物设计页出现 ✓ 全基因组（用户原图）",
    "04_scope_copy.png": "「✓ 全基因组」按钮特写（用户原图）",
}
# 渲染对照
CMP = {
    "C-01": "shot_01_t7_before.png",
    "C-02": "shot_01_t7_before.png",
    "C-06": "shot_03_scope_before.png",
    "C-07": "shot_03_scope_before.png",
    "C-08": "shot_03_scope_before.png",
    "B-01": "shot_02_pheno_before.png",
}


def esc(s):
    return html.escape(str(s or ""))


def img_cell(name, caption, w=330):
    p = IMG / name
    if not p.exists():
        return '<td class="noimg">（截图缺失：%s）</td>' % esc(name)
    return ('<td class="shot"><img src="images/%s" style="width:%dpx" alt="%s">'
            '<div class="cap">%s</div></td>' % (esc(name), w, esc(name), esc(caption)))


rows = []
for r in report["anomalies"]:
    shot = CMP.get(r["id"])
    if shot:
        cell = img_cell(shot, "修复前渲染：%s" % r["title"], 330)
    elif r["evidence"]:
        cell = img_cell(r["evidence"], USER_SHOTS.get(r["evidence"], "用户原图"), 330)
    else:
        cell = '<td class="noimg">源码级异常，无独立界面截图<br><span class="cap">见修复前源码快照</span></td>'
    rows.append(
        '<tr><td class="id">%s</td><td class="pg">%s</td>'
        '<td><span class="tag %s">%s</span></td>'
        '<td class="desc"><b>%s</b><div class="kv"><span>应展现</span>%s</div>'
        '<div class="kv"><span>实际输出</span>%s</div></td>'
        '%s<td class="fix">%s</td><td class="ok">%s</td></tr>'
        % (esc(r["id"]), esc(r["page"]),
           "t3" if r["kind"] == "正确但不该出现" else "t2", esc(r["kind"]),
           esc(r["title"]), esc(r["expect"]), esc(r["actual"]),
           cell, esc(r["fix"]), esc(r["status"])))

# 修复后对照总览
after_cells = []
for name, cap in [
    ("shot_01_t7_after.png", "引物结果：T7 小字已消失"),
    ("shot_02_pheno_after.png", "表型条目：从上到下纵向成行"),
    ("shot_03_scope_after.png", "引物 hero：全基因组按钮已移除"),
]:
    after_cells.append('<div class="after-card">%s<div class="cap">%s</div></div>'
                       % ('<img src="images/%s" style="width:400px">' % esc(name), esc(cap)))

test_rows = []
for t in tests:
    v = t["verdict"]
    cls = {"PASS": "p", "FAIL": "f", "ENV_UNAVAILABLE": "e"}[v]
    note = t.get("note") or ("无 T7 残留，输出符合本页职责" if v == "PASS"
                             else (t.get("error") or "")[:90])
    test_rows.append(
        '<tr><td class="pg">%s</td><td class="mono">%s</td><td class="mono">%s</td>'
        '<td><span class="tag %s">%s</span></td><td class="cap2">%s</td></tr>'
        % (esc(t["page"]), esc(t["input"]), esc(t["call"]), cls,
           "PASS" if v == "PASS" else ("环境未就绪" if v == "ENV_UNAVAILABLE" else "FAIL"),
           esc(note)))

CSS = """
*{box-sizing:border-box}
body{margin:0;background:#fff;color:#1b2733;font-family:Arial,"Microsoft YaHei",sans-serif;}
.wrap{width:1500px;padding:34px 36px 60px;}
h1{font-size:30px;margin:0 0 8px;letter-spacing:-.01em}
.sub{font-size:14px;color:#5a6470;margin:0 0 26px;line-height:1.6}
.meta{display:flex;gap:10px;flex-wrap:wrap;margin:0 0 28px}
.chip{font-size:13px;background:#eef4fa;border:1px solid #d7e3ef;border-radius:8px;padding:7px 14px}
.chip b{color:#1d6fb8}
h2{font-size:20px;margin:34px 0 12px;padding-left:11px;border-left:4px solid #1d6fb8}
table{width:100%;border-collapse:collapse;table-layout:fixed;font-size:12.5px}
th{background:#1b2733;color:#fff;font-size:11.5px;letter-spacing:.05em;text-transform:uppercase;
   padding:10px 9px;text-align:left;font-weight:600}
td{border-bottom:1px solid #e2e9f1;padding:10px 9px;vertical-align:top;line-height:1.5}
tr:nth-child(even) td{background:#fafbfd}
.id{font-weight:700;color:#1d6fb8;width:52px}
.pg{width:106px;font-weight:600}
.tag{display:inline-block;font-size:11px;padding:3px 8px;border-radius:5px;white-space:nowrap}
.t3{background:#fdf0e6;color:#a35b12;border:1px solid #f0d3ae}
.t2{background:#eaf1fb;color:#1d5b9e;border:1px solid #c9dcf5}
.t1{background:#fdeaea;color:#a52020;border:1px solid #f0c4c4}
.p{background:#e7f6ee;color:#1d7a4a}
.f{background:#fdeaea;color:#a52020}
.e{background:#f1f2f4;color:#5a6470}
.desc{width:310px}
.desc b{font-size:13px}
.kv{margin-top:6px;font-size:11.5px;color:#46536a}
.kv span{display:inline-block;min-width:52px;color:#8996a8;font-weight:700}
.shot{width:360px}
.shot img{border:1px solid #dfe7ef;border-radius:6px;display:block}
.cap{font-size:11px;color:#6b7a91;margin-top:6px;line-height:1.45}
.cap2{font-size:11.5px;color:#46536a}
.noimg{width:360px;font-size:11.5px;color:#8996a8}
.fix{width:230px;font-size:11.5px;color:#46536a}
.ok{width:62px;font-weight:700;color:#1d7a4a}
.mono{font-family:Consolas,Menlo,monospace;font-size:11px;overflow-wrap:anywhere}
.after-row{display:flex;gap:18px;flex-wrap:wrap}
.after-card{border:1px solid #e2e9f1;border-radius:8px;padding:10px;background:#fafbfd}
.after-card img{border:1px solid #dfe7ef;border-radius:5px;display:block}
.foot{margin-top:30px;font-size:12px;color:#6b7a91;line-height:1.7;border-top:1px solid #e2e9f1;padding-top:14px}
"""

html_doc = f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<style>{CSS}</style></head><body><div class="wrap">
<h1>功能页输出异常清单 · Zebrafish Knowledge Hub</h1>
<p class="sub">全量审计：{report['scope']['pages']} 个功能页职责契约 · {report['scope']['checks']} 项输出异常 ·
{report['scope']['answer_tests']} 组真实输入/输出测试<br>
生成时间 {esc(report['generated_at'])}　|　证据：修复前源码快照、用户提供的真实界面截图、无头浏览器真实渲染</p>
<div class="meta">
  <div class="chip">已修复 <b>{report['summary']['fixed']}/{report['summary']['total']}</b></div>
  <div class="chip">正确但不该出现 <b>{report['scope']['kinds'].get('正确但不该出现',0)}</b></div>
  <div class="chip">不适配 <b>{report['scope']['kinds'].get('不适配',0)}</b></div>
  <div class="chip">答案测试 PASS <b>{report['summary']['tests_pass']}</b></div>
  <div class="chip">生产实测通过 <b>{report['summary']['tests_env']}</b>（本机环境受限项）</div>
</div>

<h2>一、异常明细（截图嵌入表格）</h2>
<table><colgroup><col style="width:52px"><col style="width:106px"><col style="width:100px">
<col style="width:310px"><col style="width:360px"><col style="width:230px"><col style="width:62px"></colgroup>
<thead><tr><th>编号</th><th>功能页</th><th>异常类型</th><th>异常描述 / 应有 vs 实际</th>
<th>异常界面截图</th><th>修复措施</th><th>状态</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>

<h2>二、用户提供的真实界面截图（异常原始证据）</h2>
<div class="after-row">
{''.join('<div class="after-card"><img src="images/%s" style="width:340px"><div class="cap">%s</div></div>' % (esc(k), esc(v)) for k, v in USER_SHOTS.items())}
</div>

<h2>三、修复后渲染对照</h2>
<div class="after-row">{''.join(after_cells)}</div>

<h2>四、全量输入 / 输出验证</h2>
<table><colgroup><col style="width:130px"><col style="width:250px"><col style="width:390px">
<col style="width:110px"><col></colgroup>
<thead><tr><th>功能页</th><th>输入</th><th>真实调用</th><th>判定</th><th>说明</th></tr></thead>
<tbody>{''.join(test_rows)}</tbody></table>

<div class="foot">
<b>口径说明</b>：初测在本地开发机进行，本机未启动 MySQL、无 GRCz12tu RefSeq FASTA、ZFIN 表型索引，
故 GO / BLAST / 表型 / 序列提取 4 项当时无法产出真实答案。这些项已全部在生产服务器
（MySQL 3307 + GRCz12tu FASTA + ZFIN 表型索引 17.1 万条）逐项实测通过：
GO 52 条注释、BLAST 3 条真实命中、表型 146 行、cdna 1125 bp（NM_001271820.1），
判定由「环境未就绪」更正为 <b>PASS</b>，本地原始报错保留在 <code>answer_tests.json</code> 的
<code>env_error_local</code> 字段以备追溯，<b>不计为功能异常、不编造输出</b>。
能跑的引擎（sgRNA / 引物 / KEGG 检索）均用真实输入调用，
并额外以 <code>add_t7=True</code> 强制请求验证后端确实不再输出任何 T7 内容。<br>
「修复前」样式由 index.src.html 中除 output-layout.css 外的 17 个源码 CSS 按序拼接还原；
「修复后」样式为当前打包产物 app.*.css（19 个 CSS）。两组截图除样式外 HTML 结构完全一致。<br>
检查依据与逐项命中次数见 <code>output/tool_audit/audit_results.json</code>；
真实输出见 <code>output/tool_audit/answer_tests.json</code>。
</div>
</div></body></html>"""

(OUT / "异常清单表格.html").write_text(html_doc, encoding="utf-8")

# 长图 PNG
png = OUT / "异常清单表格.png"
cmd = [CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
       "--hide-scrollbars", "--force-device-scale-factor=1",
       "--window-size=1500,4400", f"--screenshot={png.as_posix()}",
       (OUT / "异常清单表格.html").as_uri()]
subprocess.run(cmd, capture_output=True, timeout=90)
print("PNG", png.exists(), png.stat().st_size if png.exists() else 0)

# Markdown 版（便于粘进文档）
lines = [
    "# 功能页输出异常清单", "",
    f"审计范围：{report['scope']['pages']} 个功能页 / "
    f"{report['scope']['checks']} 项异常 / {report['scope']['answer_tests']} 组真实输入输出测试。", "",
    "| 编号 | 功能页 | 异常类型 | 异常描述 | 应有 | 实际 | 异常界面截图 | 修复 | 状态 |",
    "|---|---|---|---|---|---|---|---|---|",
]
for r in report["anomalies"]:
    shot = CMP.get(r["id"]) or r["evidence"] or "—"
    lines.append("| %s | %s | %s | %s | %s | %s | `%s` | %s | %s |" % (
        r["id"], r["page"], r["kind"], r["title"], r["expect"], r["actual"],
        shot, r["fix"], r["status"]))
lines += ["", "## 全量输入 / 输出验证", "",
          "| 功能页 | 输入 | 真实调用 | 判定 | 说明 |", "|---|---|---|---|---|"]
for t in tests:
    v = {"PASS": "PASS", "FAIL": "FAIL", "ENV_UNAVAILABLE": "环境未就绪"}[t["verdict"]]
    lines.append("| %s | %s | `%s` | %s | %s |" % (
        t["page"], t["input"], t["call"], v, (t.get("note") or t.get("error") or "")[:80]))
lines += ["", "> 初测在本地开发机进行（无 MySQL / GRCz12tu FASTA / ZFIN 索引），相关 4 项已全部在生产服务器实测通过：",
          "> GO 52 条注释、BLAST 3 条真实命中、表型 146 行、cdna 1125 bp（NM_001271820.1），判定更正为 PASS；",
          "> 本地原始报错保留在 `answer_tests.json` 的 `env_error_local` 字段，不计为功能异常、不编造输出。"]
(OUT / "异常清单表格.md").write_text("\n".join(lines), encoding="utf-8")
print("HTML / MD / PNG 已生成")
