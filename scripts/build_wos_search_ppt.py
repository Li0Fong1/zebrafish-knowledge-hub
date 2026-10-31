# -*- coding: utf-8 -*-
"""从「斑马鱼搜索实验数据」截图生成汇报 PPT。"""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.util import Inches, Pt

SRC_DIR = Path(r"C:\Users\Lenovo\Pictures\斑马鱼搜索实验数据")
OUT_PPT = SRC_DIR / "斑马鱼文献检索实验汇报.pptx"

# 按时间顺序排列的截图
IMAGES = sorted(SRC_DIR.glob("屏幕截图 *.png"), key=lambda p: p.name)

# 幻灯片结构：(标题, 要点列表, 对应截图索引或 None)
SLIDES = [
    (
        "斑马鱼文献检索实验数据汇报",
        [
            "Web of Science 斑马鱼相关文献系统检索",
            f"汇报日期：{date.today().isoformat()}",
            "数据来源：WoS 核心合集 / 全库 / RIS 批量导出",
        ],
        None,
    ),
    (
        "一、实验目的",
        [
            "为「斑马鱼基因问答体」项目构建全面、可溯源的文献语料库",
            "系统评估不同检索词、不同数据库范围下的文献覆盖量",
            "建立可复现的分批 RIS 导出流程（单次 ≤1000 条）",
            "为后续 LLM 判别、去重合并与 RAG 向量化提供原始数据",
        ],
        None,
    ),
    (
        "二、检索平台与访问方式",
        [
            "平台：Clarivate Web of Science（中国区 webofscience.clarivate.cn）",
            "访问：湖南师范大学图书馆 VPN（yclib.hunnu.edu.cn）",
            "检索模式：Fielded Search → Topic（主题）",
            "导出格式：RIS（兼容 EndNote / Zotero / 本项目 import_wos_raw_ris）",
        ],
        None,
    ),
    (
        "三、检索策略 A：Danio rerio（核心合集）",
        [
            "检索式：TS = Danio rerio（主题字段）",
            "结果：Web of Science 核心合集 17,974 篇",
            "快速过滤：综述 1,539 · 开放获取 8,202 · Early Access 84",
            "WoS 推荐扩展词：zebrafish / danio rerio / zebrafish embryo",
        ],
        0,
    ),
    (
        "四、年代分布（核心合集 17,974 篇）",
        [
            "高峰年份：2022（1,277）· 2021（1,232）· 2025（1,218）",
            "近五年（2020–2024）年均 >1,000 篇，研究持续活跃",
            "最早可追溯至 2005 年（314 篇），呈稳步增长趋势",
            "2026 年已有 669 篇（截至检索日，含 Early Access）",
        ],
        1,
    ),
    (
        "五、检索策略 B：Danio rerio（全库扩展）",
        [
            "扩大至 All Databases（6 个合集）",
            "结果：35,091 篇（约为核心合集的 1.95 倍）",
            "MEDLINE 覆盖 30,737 篇 · 开放获取 16,290 篇 · 综述 3,398 篇",
            "说明：全库检索可捕获更多会议论文、学位论文等非核心期刊文献",
        ],
        2,
    ),
    (
        "六、分批导出流程（全库 35,091 篇）",
        [
            "WoS 单次导出上限：1,000 条/批",
            "示例批次：第 29,001 – 30,000 条（截图记录）",
            "导出内容：自定义字段（作者、标题、来源、摘要、PMID、DOI 等）",
            "需按页码循环导出直至覆盖全部记录",
        ],
        3,
    ),
    (
        "七、检索策略 C：Brachydanio rerio（旧学名）",
        [
            "检索式：TS = Brachydanio rerio（斑马鱼旧属名，1960–1990 年代文献）",
            "全库结果：2,572 篇；核心合集：1,767 篇",
            "最早文献：1964 年（Mauthner 神经元突触超微结构）",
            "与 Danio rerio 检索形成互补，避免早期文献遗漏",
        ],
        4,
    ),
    (
        "八、Brachydanio rerio 检索结果概览",
        [
            "核心合集 1,767 篇 · MEDLINE 1,659 篇",
            "综述 191 篇 · 开放获取 437 篇",
            "WoS 推荐词：brachydanio-rerio / spadetail / muscle pioneer / floating head",
            "高被引早期论文：Korfsmeier 1969（10 次引用）· Stefanelli 1964（17 次引用）",
        ],
        5,
    ),
    (
        "九、RIS 导出配置",
        [
            "格式：RIS (other reference software)",
            "记录范围：每批 1–1000 条（或指定区间）",
            "自定义字段（11 项）：Author · Title · Source · Abstract · PMID · DOI · ISSN · 被引次数等",
            "导出后文件命名：savedrecs.ris / savedrecs (N).ris",
        ],
        6,
    ),
    (
        "十、导出字段明细",
        [
            "Author(s) · Title · Source · Times Cited Count",
            "Authors Identifiers · ISSN/ISBN · PubMed ID · Conf. Info",
            "Abstract · Usage Count",
            "注：RIS 格式暂不支持全部字段，以 WoS 实际导出为准",
        ],
        7,
    ),
    (
        "十一、自定义导出字段确认",
        [
            "已勾选 11 个自定义字段（见截图）",
            "含摘要（Abstract）— 后续 LLM 斑马鱼判别必需",
            "含 PubMed ID — 便于与 PubMed 库交叉去重",
            "含被引次数 — 可用于文献质量分层",
        ],
        8,
    ),
    (
        "十二、导出操作示例（第 1–1000 条）",
        [
            "记录范围：1 to 1000",
            "记录内容：Custom selection (11)",
            "点击 Export → 下载 savedrecs.ris",
            "翻页后继续导出下一批，直至全部完成",
        ],
        9,
    ),
    (
        "十三、导出成果汇总",
        [
            "文件夹：Wos_RIS_导入/",
            "RIS 文件数：38 个（savedrecs.ris + savedrecs (1–37).ris）",
            "单文件约 1.4–2.6 MB，合计约 70 MB",
            "覆盖 Brachydanio rerio 核心合集全部 1,767 篇 + 部分扩展批次",
        ],
        10,
    ),
    (
        "十四、实验数据汇总",
        [
            "┌─────────────────────┬──────────┐",
            "│ 检索策略              │ 结果数   │",
            "├─────────────────────┼──────────┤",
            "│ Danio rerio（核心）   │ 17,974   │",
            "│ Danio rerio（全库）   │ 35,091   │",
            "│ Brachydanio rerio（核心）│  1,767   │",
            "│ Brachydanio rerio（全库）│  2,572   │",
            "│ 已导出 RIS 文件       │ 38 个    │",
            "└─────────────────────┴──────────┘",
        ],
        None,
    ),
    (
        "十五、结论与下一步",
        [
            "✓ 已完成 Brachydanio rerio 全量 RIS 导出（38 批）",
            "→ 待完成：Danio rerio 全库 35,091 篇分批导出（约 36 批）",
            "→ 导入项目：python -m scripts.import_wos_raw_ris",
            "→ 后续：LLM 斑马鱼判别 → 与 PubMed 合并去重 → RAG 向量化",
        ],
        None,
    ),
]

ACCENT = RGBColor(0x1A, 0x56, 0x8E)
DARK = RGBColor(0x22, 0x22, 0x22)
GRAY = RGBColor(0x55, 0x55, 0x55)


def _set_title(slide, text: str) -> None:
    box = slide.shapes.title
    box.text = text
    for p in box.text_frame.paragraphs:
        p.font.size = Pt(28)
        p.font.bold = True
        p.font.color.rgb = ACCENT


def _add_bullets(slide, items: list[str], left=0.6, top=1.6, width=8.8, height=5.0) -> None:
    tx = slide.shapes.add_textbox(Inches(left), Inches(top), Inches(width), Inches(height))
    tf = tx.text_frame
    tf.word_wrap = True
    for i, item in enumerate(items):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.text = item
        p.font.size = Pt(16 if not item.startswith("┌") else 13)
        p.font.color.rgb = DARK if not item.startswith("→") else GRAY
        p.space_after = Pt(8)
        if item.startswith("→"):
            p.font.italic = True


def _add_image(slide, img_path: Path, left=0.5, top=1.5, max_w=9.0, max_h=5.5) -> None:
    pic = slide.shapes.add_picture(str(img_path), Inches(left), Inches(top))
    ratio = min(max_w / (pic.width / 914400), max_h / (pic.height / 914400))
    pic.width = int(pic.width * ratio)
    pic.height = int(pic.height * ratio)
    # 居中
    slide_w = slide.part.slide_width
    pic.left = int((slide_w - pic.width) / 2)


def _title_slide(prs: Presentation) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])  # blank
    # 背景色条
    shape = slide.shapes.add_shape(1, Inches(0), Inches(0), Inches(10), Inches(7.5))
    shape.fill.solid()
    shape.fill.fore_color.rgb = RGBColor(0xF0, 0xF4, 0xF8)
    shape.line.fill.background()

    bar = slide.shapes.add_shape(1, Inches(0), Inches(0), Inches(10), Inches(0.15))
    bar.fill.solid()
    bar.fill.fore_color.rgb = ACCENT
    bar.line.fill.background()

    tx = slide.shapes.add_textbox(Inches(0.8), Inches(2.0), Inches(8.4), Inches(1.5))
    p = tx.text_frame.paragraphs[0]
    p.text = "斑马鱼文献检索实验数据汇报"
    p.font.size = Pt(36)
    p.font.bold = True
    p.font.color.rgb = ACCENT
    p.alignment = PP_ALIGN.CENTER

    sub = slide.shapes.add_textbox(Inches(0.8), Inches(3.5), Inches(8.4), Inches(2.0))
    tf = sub.text_frame
    for i, line in enumerate([
        "Web of Science 系统检索 · RIS 批量导出",
        f"汇报日期：{date.today().strftime('%Y年%m月%d日')}",
        "斑马鱼基因问答体项目 · 文献语料建设",
    ]):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.text = line
        p.font.size = Pt(18)
        p.font.color.rgb = GRAY
        p.alignment = PP_ALIGN.CENTER
        p.space_after = Pt(6)


def build() -> Path:
    if not SRC_DIR.is_dir():
        raise FileNotFoundError(SRC_DIR)
    imgs = IMAGES
    if not imgs:
        raise FileNotFoundError(f"未找到截图：{SRC_DIR}")

    prs = Presentation()
    prs.slide_width = Inches(10)
    prs.slide_height = Inches(7.5)

    _title_slide(prs)

    for title, bullets, img_idx in SLIDES[1:]:
        if img_idx is not None and img_idx < len(imgs):
            # 图文页：左文右图 或 上图下文
            slide = prs.slides.add_slide(prs.slide_layouts[6])
            bar = slide.shapes.add_shape(1, Inches(0), Inches(0), Inches(10), Inches(0.08))
            bar.fill.solid()
            bar.fill.fore_color.rgb = ACCENT
            bar.line.fill.background()

            tx = slide.shapes.add_textbox(Inches(0.4), Inches(0.2), Inches(9.2), Inches(0.7))
            p = tx.text_frame.paragraphs[0]
            p.text = title
            p.font.size = Pt(22)
            p.font.bold = True
            p.font.color.rgb = ACCENT

            _add_bullets(slide, bullets, left=0.4, top=1.0, width=4.2, height=6.0)
            pic = slide.shapes.add_picture(str(imgs[img_idx]), Inches(4.8), Inches(1.0))
            ratio = min(4.8 / (pic.width / 914400), 6.0 / (pic.height / 914400))
            pic.width = int(pic.width * ratio)
            pic.height = int(pic.height * ratio)
            pic.left = Inches(4.8)
            pic.top = Inches(1.0)
        else:
            slide = prs.slides.add_slide(prs.slide_layouts[1])
            _set_title(slide, title)
            body = slide.placeholders[1]
            tf = body.text_frame
            tf.clear()
            for i, item in enumerate(bullets):
                p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
                p.text = item
                p.font.size = Pt(15 if not item.startswith("┌") else 13)
                p.font.color.rgb = DARK
                p.space_after = Pt(6)
                if item.startswith("→"):
                    p.font.italic = True
                    p.font.color.rgb = GRAY

    prs.save(str(OUT_PPT))
    return OUT_PPT


if __name__ == "__main__":
    out = build()
    print(f"已生成：{out}")
    print(f"共 {len(SLIDES)} 页内容 + 封面")
