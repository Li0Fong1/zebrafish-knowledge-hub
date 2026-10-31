# -*- coding: utf-8 -*-
"""Build 100-question isomorph eval set from 30-base + pairs + expansions."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE30 = ROOT / "项目阶段" / "01_金标全文" / "清单与验收" / "eval_30_questions_20260901.json"
OUT = ROOT / "项目阶段" / "01_金标全文" / "清单与验收" / "eval_isomorph_100_20260901.json"

EXTRA = [
    {"type": "trait", "q": "斑马鱼条纹图案主要由哪些因素决定", "must": ["条纹"], "forbid": ["安乐死"]},
    {"type": "trait", "q": "营养不足会不会改变斑马鱼体型", "must": ["营养"], "forbid": []},
    {"type": "trait", "q": "斑马鱼头颅面部外形受哪些基因影响", "must": ["颅"], "forbid": []},
    {"type": "trait", "q": "光照背景会不会影响斑马鱼体色", "must": ["色"], "forbid": []},
    {"type": "trait", "q": "斑马鱼再生鳍的长度恢复和什么有关", "must": ["鳍"], "forbid": []},
    {"type": "trait", "q": "品系差异如何体现在斑马鱼外观上", "must": ["品系"], "forbid": []},
    {"type": "trait", "q": "斑马鱼侧线相关外形异常常见原因", "must": ["侧线"], "forbid": []},
    {"type": "trait", "q": "缺氧对斑马鱼幼鱼外形发育有何影响", "must": ["缺氧"], "forbid": []},
    {"type": "gene", "q": "mitfa 是什么基因", "must": ["mitfa"], "forbid": []},
    {"type": "gene", "q": "sox10 功能是什么", "must": ["sox10"], "forbid": []},
    {"type": "gene", "q": "bmp2b 在早期发育中做什么", "must": ["bmp"], "forbid": []},
    {"type": "gene", "q": "fgf8a 和什么过程有关", "must": ["fgf"], "forbid": []},
    {"type": "gene", "q": "tbxta 缺失常见表型", "must": ["tbx"], "forbid": []},
    {"type": "gene", "q": "pax6a 主要功能", "must": ["pax6"], "forbid": []},
    {"type": "gene", "q": "notch1a 信号大致做什么", "must": ["notch"], "forbid": []},
    {"type": "gene", "q": "vegfaa 和血管生成", "must": ["vegf"], "forbid": []},
    {"type": "gene", "q": "runx1 在造血中的角色", "must": ["runx"], "forbid": []},
    {"type": "gene", "q": "mdm2 和 tp53 的关系", "must": ["mdm2"], "forbid": []},
    {"type": "id", "q": "hif1aa 的 Ensembl ID", "must": ["ENSDARG"], "forbid": []},
    {"type": "id", "q": "asip1 的 ZFIN ID", "must": ["ZDB-GENE"], "forbid": []},
    {"type": "id", "q": "shha 官方符号确认", "must": ["shh"], "forbid": []},
    {"type": "id", "q": "myl7 别名有哪些常见写法", "must": ["myl7"], "forbid": []},
    {"type": "id", "q": "cyp1b1 的染色体位置大概在哪", "must": ["cyp1b1"], "forbid": []},
    {"type": "expr", "q": "mitfa 主要在哪些细胞表达", "must": ["色素"], "forbid": []},
    {"type": "expr", "q": "hsp70 应激诱导表达特点", "must": ["hsp"], "forbid": []},
    {"type": "expr", "q": "斑马鱼胚胎期关键基因时空表达怎么查", "must": ["表达"], "forbid": []},
    {"type": "asm", "q": "Ensembl release 116 对应哪套组装", "must": ["GRCz11"], "forbid": []},
    {"type": "asm", "q": "为什么回答基因坐标要写组装版本", "must": ["组装"], "forbid": []},
    {"type": "lit", "q": "斑马鱼再生相关近年文献怎么说", "must": ["["], "forbid": []},
    {"type": "lit", "q": "斑马鱼神经发育相关文献要点", "must": ["["], "forbid": []},
    {"type": "lit", "q": "斑马鱼毒性评价常用终点文献", "must": ["["], "forbid": []},
    {"type": "lit", "q": "斑马鱼心脏再生文献线索", "must": ["["], "forbid": []},
    {"type": "lit", "q": "斑马鱼色素细胞互作文献", "must": ["["], "forbid": []},
    {"type": "method", "q": "斑马鱼 morpholino 还适不适合做功能研究", "must": ["morpholino"], "forbid": ["安乐死"]},
    {"type": "method", "q": "斑马鱼转基因常用启动子例子", "must": ["启动"], "forbid": []},
    {"type": "method", "q": "斑马鱼原位杂交大致步骤要点", "must": ["原位"], "forbid": []},
    {"type": "compare", "q": "比较野生型与白化相关突变的外观差异", "must": ["白化"], "forbid": []},
    {"type": "compare", "q": "比较 CRISPR 与 morpholino 在斑马鱼中的用途", "must": ["CRISPR"], "forbid": []},
    {"type": "compare", "q": "比较 shha 与 shhb 功能差异", "must": ["shh"], "forbid": []},
    {"type": "gene", "q": "fkbp10a 和骨骼表型", "must": ["fkbp"], "forbid": []},
    {"type": "trait", "q": "斑马鱼体长生长速度受哪些环境因素影响", "must": ["生长"], "forbid": []},
    {"type": "lit", "q": "斑马鱼肠道微生物与宿主互作文献", "must": ["["], "forbid": []},
    {"type": "expr", "q": "tp53 在 DNA 损伤后表达如何变化", "must": ["tp53"], "forbid": []},
    {"type": "id", "q": "ncbi gene id 30590 对应符号", "must": ["tp53"], "forbid": []},
    {"type": "method", "q": "斑马鱼 CRISPR 敲入大致注意什么", "must": ["CRISPR"], "forbid": []},
    {"type": "trait", "q": "社会等级会不会影响斑马鱼外观相关生长", "must": ["等级"], "forbid": []},
    {"type": "gene", "q": "lepa 和能量代谢", "must": ["lep"], "forbid": []},
    {"type": "lit", "q": "斑马鱼肿瘤模型相关文献", "must": ["["], "forbid": []},
    {"type": "asm", "q": "本系统基因条目大约多少量级", "must": ["基因"], "forbid": []},
    {"type": "fact", "q": "斑马鱼全基因组蛋白编码基因大约多少", "must": ["蛋白"], "forbid": []},
]

pairs = json.loads(OUT.read_text(encoding="utf-8")).get("isomorphic_pairs") or []
base = json.loads(BASE30.read_text(encoding="utf-8"))["questions"]

seen = set()
questions = []


def add(item, *, pair_id=None, side=None):
    q = item["q"].strip()
    if q in seen:
        return
    seen.add(q)
    row = {
        "id": len(questions) + 1,
        "type": item.get("type") or "trait",
        "q": q,
        "must": item.get("must") or [],
        "forbid": item.get("forbid") or [],
    }
    if pair_id:
        row["pair_id"] = pair_id
        row["side"] = side
    questions.append(row)


for it in base:
    add(it)

# isomorphic zebrafish legs
for p in pairs:
    add(
        {
            "type": p.get("type") or "trait",
            "q": p["zebrafish"],
            "must": ["["] if p.get("type") in ("lit", "trait", "method") else [],
            "forbid": ["安乐死"] if p.get("type") in ("trait", "method") else [],
        },
        pair_id=p["pair_id"],
        side="zebrafish",
    )

for it in EXTRA:
    add(it)

# pad to 100 with paraphrases if short
pad = [
    {"type": "trait", "q": "斑马鱼外观整齐度和饲养管理有关吗", "must": ["饲养"], "forbid": []},
    {"type": "gene", "q": "sox9a 功能简述", "must": ["sox9"], "forbid": []},
    {"type": "lit", "q": "斑马鱼行为表型遗传文献", "must": ["["], "forbid": []},
    {"type": "method", "q": "斑马鱼活体成像为什么常用透明品系", "must": ["透明"], "forbid": []},
    {"type": "compare", "q": "比较营养充足与限食下的斑马鱼体型", "must": ["营养"], "forbid": []},
    {"type": "trait", "q": "斑马鱼幼鱼体长差异常见环境原因", "must": ["体长"], "forbid": []},
    {"type": "gene", "q": "ednrb 与色素相关吗", "must": ["色"], "forbid": []},
    {"type": "lit", "q": "斑马鱼骨骼发育关键文献", "must": ["["], "forbid": []},
    {"type": "id", "q": "bmp4 的 Ensembl ID", "must": ["ENSDARG"], "forbid": []},
    {"type": "expr", "q": "胰岛素相关基因表达部位简述", "must": ["表达"], "forbid": []},
    {"type": "method", "q": "斑马鱼显微注射用途", "must": ["注射"], "forbid": []},
    {"type": "trait", "q": "水流刺激会不会影响斑马鱼体型相关性状", "must": ["水流"], "forbid": []},
    {"type": "gene", "q": "wnt8a 早期功能", "must": ["wnt"], "forbid": []},
    {"type": "compare", "q": "比较 grcz11 注释与文献旧组装引用", "must": ["GRCz"], "forbid": []},
    {"type": "lit", "q": "斑马鱼免疫细胞发育文献要点", "must": ["["], "forbid": []},
]
for it in pad:
    if len(questions) >= 100:
        break
    add(it)

while len(questions) > 100:
    questions.pop()

# renumber
for i, q in enumerate(questions, 1):
    q["id"] = i

payload = {
    "id": "eval_isomorph_100_20260901",
    "note": "对标马铃薯：100 题大样本自动指标 + 20 对同构题盲评。含原 30 题。",
    "rubric": json.loads(OUT.read_text(encoding="utf-8"))["rubric"],
    "isomorphic_pairs": pairs,
    "questions": questions,
}
OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
print("wrote", OUT, "n=", len(questions), "pairs=", len(pairs))
