# -*- coding: utf-8 -*-
"""把评委分片结果合并成一份（分片并行跑完用）。

用法：python scripts\\merge_judge_parts.py [模型后缀]    默认 reasoner
输出：Figure/_config_sweep_scores_<后缀>.json
自检：每个 (题号, 配置) 只能出现一次；缺格会明确报出（不静默补）。
"""
from __future__ import annotations

import glob
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CFG = ["rag_partial", "rag_full", "fusion_partial", "fusion_full", "hub_partial", "hub_full"]


def main() -> int:
    tag = (sys.argv[1] if len(sys.argv) > 1 else "reasoner").strip("_")
    parts = sorted(glob.glob(str(ROOT / "Figure" / f"_config_sweep_scores_{tag}_p*.json")))
    if not parts:
        print(f"[merge] 没有分片文件（Figure/_config_sweep_scores_{tag}_p*.json）")
        return 2
    rows, dims, maxtotal, model, skipped = [], None, None, None, []
    for p in parts:
        d = json.loads(Path(p).read_text(encoding="utf-8"))
        rows.extend(d.get("rows") or [])
        dims = dims or d.get("dims")
        maxtotal = maxtotal or d.get("max_total")
        model = model or d.get("model")
        skipped.extend(d.get("skipped_qids") or [])
        print(f"  {Path(p).name}: {len(d.get('rows') or [])} 条")
    key = [(r["qid"], r["config"]) for r in rows]
    dup = {k for k in key if key.count(k) > 1}
    if dup:
        print(f"[merge] 有重复单元格 {sorted(dup)}，中止")
        return 3
    qids = sorted({r["qid"] for r in rows})
    miss = [(q, c) for q in qids for c in CFG if (q, c) not in set(key)]
    out = ROOT / "Figure" / f"_config_sweep_scores_{tag}.json"
    out.write_text(json.dumps({"dims": dims, "max_total": maxtotal, "model": model,
                               "parts": [Path(p).name for p in parts],
                               "skipped_qids": sorted(set(skipped)), "rows": rows},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[merge] {len(rows)} 条（{len(qids)} 题）-> {out}")
    if miss:
        print(f"[merge] 仍缺 {len(miss)} 格: {miss}")
        return 4
    print("[merge] 覆盖完整 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
