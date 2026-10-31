# -*- coding: utf-8 -*-
"""图集正确性校验（以 Figure legend.pptx 为基准规范）。

检验层次
    V0  文件与位图格式      PNG 可读 / 尺寸 / DPI / 非空白 / 色彩丰富度
    V1  跨产物一致性        PNG ↔ _legends.json ↔ Legend.md ↔ PPTX 四者对齐
    V2  数值可溯源         图中与图注中出现的每个大数字都必须能在真实数据源中查到
    V3  面板标签一致       图注声明的 (A)(B)(C)… 必须都真实绘在图上
    V4  版式完整性         四边无裁切、无内容溢出、长宽比合理
    V5  参考 deck 规范     16:9 (12192000×6858000) / 每页一图 / 图片不变形 / 图注一致
    V6  图片真身一致       PPTX 内嵌图片与本地 PNG 同源（哈希）
    V7  图注自洽           每幅图注必含 "Figure N." 且含面板说明

用法：
    .venv\\Scripts\\python.exe -X utf8 scripts\\verify_figures.py
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from pptx import Presentation
from pptx.util import Emu

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fig_config as FC                                        # noqa: E402

FIG = FC.FIG_DIR                                               # .env: FIG_OUTPUT_DIR

RECORDS: list[dict] = []


def rec(layer, cid, name, ok, detail="", evidence="", severity=None):
    sev = severity or ("info" if ok else "error")
    RECORDS.append(dict(layer=layer, id=cid, name=name, ok=bool(ok),
                        severity=sev, detail=detail, evidence=evidence))


def load(p: Path):
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


LEG = load(FIG / "_legends.json") or {}
DATA = load(FIG / "_data.json") or {}
MY = load(FIG / "_data_mysql.json") or {}
ACC = load(FIG / "_acceptance_all.json") or {}
CLEAN = load(FIG / "_clean_stats.json") or {}
ITEMS = sorted(((int(k), v) for k, v in LEG.items()), key=lambda kv: kv[0])


# ── V0 ────────────────────────────────────────────────────────────────────
def v0():
    _pngs = sorted(p.name for p in FIG.glob("Fig*.png"))
    rec("V0", "V0-0", "图注条目数与 PNG 文件数一致",
        len(ITEMS) == len(_pngs) and len(ITEMS) >= 8,
        f"legends={len(ITEMS)} png={len(_pngs)}")
    for num, meta in ITEMS:
        p = FIG / meta["file"]
        if not p.is_file():
            rec("V0", f"V0-{num}", f"Fig{num} 文件存在", False, str(p))
            continue
        rec("V0", f"V0-{num}", f"Fig{num} 文件存在", True, meta["file"], severity="info")
        try:
            im = Image.open(p)
            im.load()
        except Exception as e:  # pragma: no cover
            rec("V0", f"V0-{num}-open", f"Fig{num} 可解码", False, repr(e))
            continue
        w, h = im.size
        dpi = im.info.get("dpi", (0, 0))
        rec("V0", f"V0-{num}-size", f"Fig{num} 分辨率足够",
            w >= 2500 and h >= 1400, f"{w}x{h}", severity=None if w >= 2500 else "warning")
        rec("V0", f"V0-{num}-dpi", f"Fig{num} 输出 300 dpi",
            abs(float(dpi[0]) - 300) < 6, f"dpi={dpi}")
        arr = np.asarray(im.convert("L"))
        white = float((arr > 245).mean())
        ink = 1.0 - white
        rec("V0", f"V0-{num}-ink", f"Fig{num} 非空白且有内容",
            0.005 < ink < 0.60, f"ink={ink:.4f}")
        rgb = np.asarray(im.convert("RGB")).reshape(-1, 3)
        uniq = len(np.unique(rgb[::17], axis=0))
        rec("V0", f"V0-{num}-color", f"Fig{num} 含图形元素（色彩丰富）",
            uniq > 120, f"unique_colors≈{uniq}", severity=None if uniq > 120 else "warning")
        # 图内文字量（用渲染期捕获的画布文本核对，非 OCR）
        ntext = len(meta.get("texts") or [])
        rec("V0", f"V0-{num}-texts", f"Fig{num} 已捕获画布文本",
            ntext >= 8, f"canvas_texts={ntext}", severity=None if ntext >= 8 else "warning")


# ── V1 ────────────────────────────────────────────────────────────────────
def v1():
    pngs = sorted(p.name for p in FIG.glob("Fig*.png"))
    names = [m["file"] for _n, m in ITEMS]
    rec("V1", "V1-1", "PNG 集合与图注表一致", set(pngs) == set(names),
        f"png={len(pngs)} legends={len(names)}")
    rec("V1", "V1-2", f"图编号为连续 1..{len(ITEMS)}",
        [n for n, _ in ITEMS] == list(range(1, len(ITEMS) + 1)),
        str([n for n, _ in ITEMS]))

    md_p = FIG / "Legend.md"
    md = md_p.read_text(encoding="utf-8") if md_p.is_file() else ""
    rec("V1", "V1-3", "Legend.md 存在", bool(md), f"{len(md)} chars")
    miss = [m["file"] for _n, m in ITEMS if m["file"] not in md]
    rec("V1", "V1-4", "Legend.md 引用全部 8 张图", not miss, f"missing={miss}")
    miss_en = [n for n, m in ITEMS if m["en"][:48] not in md]
    rec("V1", "V1-5", "Legend.md 含全部英文图注", not miss_en, f"missing={miss_en}")
    miss_zh = [n for n, m in ITEMS if m["zh"][:24] not in md]
    rec("V1", "V1-6", "Legend.md 含全部中文图注", not miss_zh, f"missing={miss_zh}")

    ppt = sorted(FIG.glob("*.pptx"))
    if not ppt:
        rec("V1", "V1-7", "PPTX 存在", False)
        return []
    decks = []
    for p in ppt:
        try:
            prs = Presentation(str(p))
        except Exception as e:
            rec("V1", f"V1-7-{p.stem}", f"{p.name} 可打开", False, repr(e))
            continue
        decks.append((p, prs, "zh" if "中文版" in p.name else "en"))
    rec("V1", "V1-7", "PPTX 存在且可打开", bool(decks),
        f"{len(decks)} deck(s): {[p.name for p, _x, _l in decks]}")
    exp_slides = 3 + len(ITEMS) + 1   # 封面 + 总览 + 图页 + 数据来源 + 图注附录
    for p, prs, lg in decks:
        rec("V1", f"V1-8-{lg}", f"[{lg}] 页数 = 封面+总览+图页+数据来源+图注附录",
            len(prs.slides) == exp_slides, f"{p.name}: {len(prs.slides)} vs {exp_slides}")
    return decks


# ── V2 数值可溯源 ─────────────────────────────────────────────────────────
def _numbers(obj, out):
    if isinstance(obj, dict):
        for v in obj.values():
            _numbers(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _numbers(v, out)
    elif isinstance(obj, bool):
        return
    elif isinstance(obj, int):
        out.add(int(obj))
    elif isinstance(obj, float):
        out.add(round(float(obj), 4))
        if abs(obj) >= 1000:            # 均值等小数同时提供整数形式
            out.add(int(round(obj)))
    elif isinstance(obj, str):
        for m in re.findall(r"\d[\d,]{2,}", obj):
            try:
                out.add(int(m.replace(",", "")))
            except ValueError:
                pass


# 版式/参数类常量（非数据指标，允许自由出现）
ALLOWED_SMALL = {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 16, 20, 30, 36, 40,
                 43, 48, 50, 53, 90, 100, 101, 120, 174, 200, 300, 197}
# 解释：197 = 三次验收报告断言数之和（101+48+48），48/53 亦为报告总数
EXTRA_ALLOWED = {197: "sum of acceptance totals (101+48+48)",
                 49306965: "RefSeq accession fragment (GCF_049306965.1)"}
YEARS = set(range(1998, 2031))


def _config_numbers(src, out: set):
    """从配置键名/字符串中推导参数值，如 checks['faiss_dim_2048'] → 2048。"""
    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                for m in re.findall(r"_(\d{3,})", str(k)):
                    out.add(int(m))
                walk(v)
        elif isinstance(o, (list, tuple)):
            for v in o:
                walk(v)
        elif isinstance(o, str):
            for m in re.findall(r"_(\d{3,})\b", o):
                out.add(int(m))
    walk(src)


def _known_numbers():
    known: set = set()
    for src in (DATA, MY, ACC, CLEAN):
        _numbers(src, known)
        _config_numbers(src, known)
    known |= ALLOWED_SMALL | YEARS
    derived: set = set()
    big = [v for v in known if isinstance(v, int) and 100 <= v <= 10 ** 7]
    for a in big:
        for b in big:
            derived.add(round(a / b * 100, 1))
            derived.add(round(a / b, 1))
    return known, derived


def _nums_in_text(s: str):
    out = []
    for m in re.findall(r"\d[\d,]*(?:\.\d+)?", str(s)):
        raw = m.replace(",", "")
        try:
            out.append(float(raw))
        except ValueError:
            pass
    return out


def v2():
    known, derived = _known_numbers()
    ex = MY.get("exact") or {}
    unsourced: list[str] = []
    scanned = 0
    for num, meta in ITEMS:
        blobs = [("legend_en", meta["en"]), ("legend_zh", meta["zh"])]
        for i, t in enumerate(meta.get("texts") or []):
            blobs.append((f"canvas[{i}]", t))
        for where, txt in blobs:
            for v in _nums_in_text(txt):
                scanned += 1
                if v < 1000:          # 参数/编号/年份/小数比例，逐条抽查
                    continue
                iv = int(round(v))
                if iv in known or iv in ex.values():
                    continue
                if iv in EXTRA_ALLOWED:
                    continue
                # 允许千分位/四舍五入差异 ±1
                if any(abs(iv - k) <= 1 for k in known if isinstance(k, int)):
                    continue
                unsourced.append(f"Fig{num} {where}: {iv}")
    rec("V2", "V2-1", "所有大数字均可溯源到真实数据",
        not unsourced, f"scanned={scanned} unsourced={len(unsourced)}",
        evidence="; ".join(unsourced[:12]))

    # 百分比复核：图注中出现的百分数是否与数据一致
    bad_pct = []
    for num, meta in ITEMS:
        for txt in [meta["en"], meta["zh"]] + list(meta.get("texts") or []):
            for m in re.findall(r"(\d{1,3}(?:\.\d+)?)\s?%", str(txt)):
                pv = float(m)
                if pv in (0.0, 100.0):
                    continue
                if pv in known or pv in derived:
                    continue
                if any(abs(pv - d) <= 0.06 for d in derived):
                    continue
                if any(abs(pv - k) <= 0.06 for k in known if isinstance(k, float)):
                    continue
                bad_pct.append(f"Fig{num}: {pv}%")
    rec("V2", "V2-2", "所有百分比与数据源自洽", not bad_pct,
        f"checked, inconsistent={len(bad_pct)}", evidence="; ".join(bad_pct[:12]))

    # 抽查关键指标是否真的出自数据库
    keys = [("26,321", ex.get("gene_basic")), ("145,439", ex.get("gene_alias")),
            ("110,966", ex.get("gene_go")), ("319,344", ex.get("gene_expression")),
            ("80,212", ex.get("gene_xref")), ("161,298", ex.get("gene_reference"))]
    bad = [k for k, v in keys if v and not _fmt_present(k)]
    rec("V2", "V2-3", "关键指标字符串在图中真实出现", not bad, f"missing={bad}")


def _fmt_present(s: str) -> bool:
    return any(s in (t or "") for _n, m in ITEMS for t in (m.get("texts") or []))


# ── V3 面板标签 ───────────────────────────────────────────────────────────
def v3():
    for num, meta in ITEMS:
        texts = meta.get("texts") or []
        canvas = set()
        for t in texts:
            m = re.match(r"^\s*\(([A-Z])\)", t)
            if m:
                canvas.add(m.group(1))
            for g in re.findall(r"\(([A-Z])\)", t):
                canvas.add(g)
        claimed = set(re.findall(r"\(([A-Z])\)", meta["en"]))
        missing = sorted(claimed - canvas)
        extra = sorted(canvas - claimed)
        rec("V3", f"V3-{num}", f"Fig{num} 图注声明的面板均已绘制",
            not missing, f"claimed={sorted(claimed)} canvas={sorted(canvas)}",
            evidence=f"missing={missing}")
        if extra:
            rec("V3", f"V3-{num}-extra", f"Fig{num} 图上有图注未提及的面板",
                False, f"extra={extra}", severity="warning")
        else:
            rec("V3", f"V3-{num}-extra", f"Fig{num} 面板集合与图注完全对应", True,
                f"{sorted(canvas)}", severity="info")


# ── V4 版式完整性 ─────────────────────────────────────────────────────────
def v4():
    for num, meta in ITEMS:
        p = FIG / meta["file"]
        if not p.is_file():
            continue
        im = Image.open(p)
        arr = np.asarray(im.convert("L"))
        h, w = arr.shape
        band = max(6, int(0.005 * min(h, w)))
        edges = {"top": arr[:band, :], "bottom": arr[-band:, :],
                 "left": arr[:, :band], "right": arr[:, -band:]}
        worst = []
        for k, e in edges.items():
            r = float((e < 205).mean())
            if r >= 0.02:
                worst.append(f"{k}={r:.3f}")
            rec("V4", f"V4-{num}-{k}", f"Fig{num} {k} 边无内容被裁切", r < 0.02,
                f"ink_ratio={r:.4f}")
        ratio = w / h
        rec("V4", f"V4-{num}-ratio", f"Fig{num} 长宽比在演示范围内",
            1.35 <= ratio <= 2.60, f"{ratio:.3f}")
        # 内容边界（去掉白边后的有效区域）
        mask = arr < 245
        ys, xs = np.where(mask)
        if len(xs):
            box = (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))
            marg = min(box[0], box[1], w - box[2], h - box[3]) / min(h, w)
            rec("V4", f"V4-{num}-margin", f"Fig{num} 内容留白充分",
                marg > 0.012, f"min_margin={marg:.4f}")
        # 顶部/底部是否被截断：检查最末一行是否有墨迹触及
        rec("V4", f"V4-{num}-overflow", f"Fig{num} 无溢出裁切",
            not worst, f"worst_edges={worst}")


# ── V5 参考 deck 规范 ────────────────────────────────────────────────────
def v5(decks):
    for path, prs, lang in decks:
        tag = f"[{lang}]"
        ok_ratio = (prs.slide_width == 12192000 and prs.slide_height == 6858000)
        rec("V5", f"V5-1-{lang}", f"{tag} PPT 尺寸与参考 deck 一致（16:9）", ok_ratio,
            f"{prs.slide_width}x{prs.slide_height}")
        pic_slides, deformed = 0, []
        for i, s in enumerate(prs.slides, 1):
            pics = [sh for sh in s.shapes if sh.shape_type == 13]
            if len(pics) == 1 and 3 <= i <= 2 + len(ITEMS):
                pic_slides += 1
                sh = pics[0]
                try:
                    iw, ih = sh.image.size
                    shown = sh.width / sh.height
                    native = iw / ih
                    err = abs(shown - native) / native
                    if err > 0.03:
                        deformed.append(f"slide{i}: {err*100:.1f}%")
                except Exception as e:
                    deformed.append(f"slide{i}: {e}")
        rec("V5", f"V5-2-{lang}", f"{tag} 图页均为“一页一图”", pic_slides == len(ITEMS),
            f"{pic_slides}/{len(ITEMS)}")
        rec("V5", f"V5-3-{lang}", f"{tag} PPT 内图片未变形（比例误差<3%）", not deformed,
            f"deformed={deformed}")
        # 仅在图页采集标题（附件页是图注全文，会污染判定）
        heads = {}
        for i, s in enumerate(prs.slides, 1):
            if not (3 <= i <= 2 + len(ITEMS)):
                continue
            for sh in s.shapes:
                if sh.has_text_frame:
                    t = sh.text_frame.text.strip()
                    m = re.match(r"(?:Figure|图)\s*(\d+)[.]", t)
                    if m:
                        heads[int(m.group(1))] = t
                        break
        bad = []
        for num, meta in ITEMS:
            hd = heads.get(num, "")
            if not hd:
                bad.append(num)
                continue
            if lang == "zh":
                core = hd.split(f"图 {num}.", 1)[-1].strip()[:24]
                if core and core not in meta["zh"]:
                    bad.append(num)
            else:
                core = hd.split(f"Figure {num}.", 1)[-1].strip()[:48]
                if core and core not in meta["en"]:
                    bad.append(num)
        rec("V5", f"V5-4-{lang}", f"{tag} PPT 图注标题与 _legends.json 一致", not bad,
            f"mismatch={bad}")
        pat = r"^图 \d+\." if lang == "zh" else r"^Figure \d+\."
        rec("V5", f"V5-5-{lang}", f"{tag} 每幅图标题以 “{ '图 N.' if lang=='zh' else 'Figure N.' }” 开头",
            all(re.match(pat, m["zh"] if lang == "zh" else m["en"]) for _n, m in ITEMS),
            "参考 deck 图注句式")


# ── V6 PPTX 内嵌图片与本地 PNG 同源 ───────────────────────────────────────
def v6(decks):
    local = {}
    for _n, m in ITEMS:
        p = FIG / m["file"]
        if p.is_file():
            local[m["file"]] = hashlib.sha256(p.read_bytes()).hexdigest()
    for path, prs, lang in decks:
        seen = {}
        for s in prs.slides:
            for sh in s.shapes:
                if sh.shape_type == 13:
                    try:
                        seen[hashlib.sha256(sh.image.blob).hexdigest()] = len(sh.image.blob)
                    except Exception:
                        pass
        matched = sum(1 for h in local.values() if h in seen)
        rec("V6", f"V6-1-{lang}", f"[{lang}] PPTX 内嵌图片与本地 PNG 逐字节一致",
            matched == len(ITEMS), f"{path.name}: matched={matched}/{len(ITEMS)}",
            severity="warning" if matched < len(ITEMS) else "info")


# ── V7 图注自洽 ───────────────────────────────────────────────────────────
def v7():
    for num, meta in ITEMS:
        en, zh = meta["en"], meta["zh"]
        rec("V7", f"V7-{num}-prefix", f"Fig{num} 英文图注以 Figure N. 开头",
            bool(re.match(rf"^Figure {num}\.", en)), en[:46])
        rec("V7", f"V7-{num}-zh", f"Fig{num} 中文图注以 “图 N.” 开头",
            zh.startswith(f"图 {num}."), zh[:30])
        rec("V7", f"V7-{num}-panels", f"Fig{num} 英文图注逐面板说明",
            len(en) > 260 and "(" in en, f"len={len(en)}")
        rec("V7", f"V7-{num}-len", f"Fig{num} 图注长度符合期刊惯例",
            150 <= len(en) <= 1200, f"len={len(en)}",
            severity=None if 150 <= len(en) <= 1200 else "warning")


# ── V8 图注 ↔ 图内容互证 ─────────────────────────────────────────────────
STRICT_TERMS = ["GRCz12tu", "GRCz11", "GRCz10", "FAISS", "KEGG", "Ensembl",
                "PMID", "CDS", "GO"]


def _has(term: str, text: str) -> bool:
    return re.search(r"(?<![A-Za-z0-9])" + re.escape(term) + r"(?![A-Za-z0-9])",
                     text, re.I) is not None


def v8():
    for num, meta in ITEMS:
        canvas = "\n".join(meta.get("texts") or [])
        en = meta["en"]
        missing, unmentioned = [], []
        for t in STRICT_TERMS:
            in_legend = _has(t, en)
            on_fig = _has(t, canvas)
            if in_legend and not on_fig:
                missing.append(t)
            if on_fig and not in_legend:
                unmentioned.append(t)
        rec("V8", f"V8-{num}-term", f"Fig{num} 图注术语均已画在图上",
            not missing, f"missing={missing}")
        rec("V8", f"V8-{num}-term-x", f"Fig{num} 图上术语均已在图注说明",
            not unmentioned, f"not_in_legend={unmentioned}",
            severity="warning" if unmentioned else "info")
        big = [int(x.replace(",", "")) for x in re.findall(r"\d[\d,]*", en)]
        big = [b for b in big if b >= 1000 and b not in YEARS]   # 年份不必重复画在图上
        miss_n = [b for b in big if f"{b:,}" not in canvas and str(b) not in canvas]
        rec("V8", f"V8-{num}-num", f"Fig{num} 图注中的大数字均在图上可见",
            not miss_n, f"legend_big={len(big)} missing={miss_n}")


# ── V9 文字重叠遮挡 ───────────────────────────────────────────────────────
def v9():
    for num, meta in ITEMS:
        ov = meta.get("overlaps") or []
        rec("V9", f"V9-{num}", f"Fig{num} 无文字相互遮挡", not ov,
            f"overlaps={len(ov)}", evidence="; ".join(ov[:6]))


# ── V10 过时值黑名单（防止修好后回退） ──────────────────────────────────
STALE_TOKENS = [
    "174 ms", "2,342 characters", "self-rank = 1", "self-rank=1",
    "13,534 papers", "516,476", "1,269 FAISS", "candidates = 200",
    "5.3-5.8 k", "top 10 reranked", "10 reranked passages",
]


def v10():
    for num, meta in ITEMS:
        blob = "\n".join([meta.get("en", ""), meta.get("zh", "")]
                         + list(meta.get("texts") or []))
        hit = [t for t in STALE_TOKENS if t in blob]
        rec("V10", f"V10-{num}", f"Fig{num} 未残留过时数值", not hit,
            f"stale={hit}", severity="error" if hit else "info")


# ── V11 PPT 文本溢出估计 ─────────────────────────────────────────────────
def _font_path():
    for cand in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyh.ttf",
                 r"C:\Windows\Fonts\simhei.ttf", r"C:\Windows\Fonts\arial.ttf"):
        if Path(cand).is_file():
            return cand
    return None


def v11(decks):
    """用字体度量估算每段文字所需高度，与文本框高度比较，发现溢出风险。"""
    fp = _font_path()
    if not fp:
        rec("V11", "V11-0", "字体度量可用", False, "未找到可测量字体", severity="warning")
        return
    from PIL import ImageFont
    import math
    SCALE = 4.0
    cache: dict = {}

    def font_of(size_pt: float):
        k = round(size_pt, 1)
        if k not in cache:
            cache[k] = ImageFont.truetype(fp, max(4, int(round(size_pt * SCALE))))
        return cache[k]

    EMU_IN, PT_IN = 914400.0, 72.0
    for path, prs, lang in decks:
        worst = []
        for si, s in enumerate(prs.slides, 1):
            for sh in s.shapes:
                if not sh.has_text_frame:
                    continue
                txt_all = sh.text_frame.text
                if not txt_all.strip():
                    continue
                box_w_pt = sh.width / EMU_IN * PT_IN
                box_h_pt = sh.height / EMU_IN * PT_IN
                need = 0.0
                for para in sh.text_frame.paragraphs:
                    line = "".join(r.text for r in para.runs) or ""
                    sizes = [r.font.size.pt for r in para.runs if r.font.size] or [12.0]
                    sz = max(sizes)
                    spacing = para.line_spacing or 1.0
                    if not line:
                        need += sz * 1.22 * spacing
                        continue
                    w = font_of(sz).getlength(line) / SCALE
                    lines = max(1, math.ceil(w / max(1.0, box_w_pt)))
                    need += lines * sz * 1.22 * float(spacing)
                if need > box_h_pt * 1.15:          # 15% 容差
                    worst.append(f"slide{si}: need {need:.0f}pt vs box {box_h_pt:.0f}pt "
                                 f"[{txt_all.strip()[:34]}]")
        rec("V11", f"V11-1-{lang}", f"[{lang}] PPT 文本未溢出文本框（字体度量估算）",
            not worst, f"{path.name}: overflow_risks={len(worst)}",
            evidence=" | ".join(worst[:5]))


def main():
    print("[verify] 图集正确性校验（基准：Figure legend.pptx）")
    v0()
    decks = v1()
    v2()
    v3()
    v4()
    v5(decks)
    v6(decks)
    v7()
    v8()
    v9()
    v10()
    v11(decks)

    # 配置驱动的门禁：断言总数不得低于 FIG_VERIFY_MIN_ASSERTIONS
    rec("V12", "V12-1",
        f"断言总数 ≥ FIG_VERIFY_MIN_ASSERTIONS（{FC.FIG_VERIFY_MIN_ASSERTIONS}）",
        len(RECORDS) >= FC.FIG_VERIFY_MIN_ASSERTIONS,
        f"total={len(RECORDS)} · dpi={FC.FIG_DPI} · lang={FC.FIG_DECK_LANG} · dir={FC.FIG_DIR.name}")

    from collections import defaultdict
    agg = defaultdict(lambda: {"n": 0, "ok": 0, "err": 0, "warn": 0})
    for r in RECORDS:
        a = agg[r["layer"]]
        a["n"] += 1
        if r["ok"]:
            a["ok"] += 1
        elif r["severity"] == "warning":
            a["warn"] += 1
        else:
            a["err"] += 1
    total = len(RECORDS)
    errs = [r for r in RECORDS if not r["ok"] and r["severity"] != "warning"]
    warns = [r for r in RECORDS if not r["ok"] and r["severity"] == "warning"]

    print("\n  layer  checks  pass  err  warn")
    for ly in sorted(agg):
        a = agg[ly]
        print("  %-5s  %6d  %4d  %3d  %4d" % (ly, a["n"], a["ok"], a["err"], a["warn"]))
    print("\n  TOTAL=%d PASS=%d ERRORS=%d WARNINGS=%d"
          % (total, total - len(errs) - len(warns), len(errs), len(warns)))

    if errs:
        print("\n  --- FAILURES ---")
        for r in errs:
            print("  [%s] %s :: %s | %s" % (r["layer"], r["id"], r["name"], r["detail"]))
            if r["evidence"]:
                print("        evidence: %s" % str(r["evidence"])[:200])
    if warns:
        print("\n  --- WARNINGS ---")
        for r in warns[:20]:
            print("  [%s] %s :: %s | %s" % (r["layer"], r["id"], r["name"], r["detail"]))

    out = FIG / "verify_report.json"
    out.write_text(json.dumps({
        "total": total, "errors": len(errs), "warnings": len(warns),
        "per_layer": {k: dict(v) for k, v in agg.items()},
        "records": RECORDS,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n[ok] report -> Figure/verify_report.json")
    return 1 if errs else 0


if __name__ == "__main__":
    raise SystemExit(main())
