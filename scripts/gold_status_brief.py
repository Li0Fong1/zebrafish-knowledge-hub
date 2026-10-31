# -*- coding: utf-8 -*-
import json
import subprocess
from pathlib import Path

from scripts.gold_hunt_fulltext import is_pdf_gold

ROOT = Path(r"d:\PycharmProjects\斑马鱼基因问答体")
OUT = ROOT / "解析文件"
YES = OUT / "yes_fulltext_gold"
PDFKU = OUT / "pdfku_library_fulltext_gold"


def ps():
    from scripts.win_hidden import python_procs

    return python_procs(include_pythonw=False)


def tail(path: Path, n: int = 6) -> list[str]:
    if not path.is_file():
        return []
    return path.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]


def count_corpus(root: Path) -> tuple[int, int]:
    gold = notg = 0
    for p in root.glob("batch_*/papers/*.json"):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            notg += 1
            continue
        if is_pdf_gold(rec):
            gold += 1
        else:
            notg += 1
    return gold, notg


def main() -> None:
    alive = []
    for r in ps():
        c = r.get("CommandLine") or ""
        if "gold_" in c:
            alive.append(f"{r.get('ProcessId')} {c[:130]}")

    yg, yn = count_corpus(YES)
    pg, pn = count_corpus(PDFKU)

    audit = {}
    ap = OUT / "gold_3h_audit_latest.json"
    if ap.is_file():
        audit = json.loads(ap.read_text(encoding="utf-8"))

    hist = []
    hp = OUT / "gold_3h_audit_history.jsonl"
    if hp.is_file():
        hist = [
            json.loads(x)
            for x in hp.read_text(encoding="utf-8").splitlines()
            if x.strip()
        ][-6:]

    hunt = pdfku = None
    hs = YES / "hunt_fulltext_status.json"
    psj = PDFKU / "status.json"
    if hs.is_file():
        hunt = json.loads(hs.read_text(encoding="utf-8"))
    if psj.is_file():
        pdfku = json.loads(psj.read_text(encoding="utf-8"))

    out = {
        "alive_n": len(alive),
        "alive": alive,
        "live": {
            "yes_gold": yg,
            "yes_not": yn,
            "yes_pct": round(100 * yg / (yg + yn), 2) if yg + yn else 0,
            "pdfku_gold": pg,
            "pdfku_not": pn,
            "pdfku_pct": round(100 * pg / (pg + pn), 2) if pg + pn else 0,
            "total_gold": yg + pg,
            "total_not": yn + pn,
        },
        "last_audit_at": audit.get("updated_at"),
        "last_audit_delta": (audit.get("patch") or {}).get("gold_delta"),
        "last_audit_actions": (audit.get("patch") or {}).get("actions"),
        "history": hist,
        "hunt_status": hunt,
        "pdfku_status": pdfku,
        "keep_tail": tail(OUT / "gold_keep_alive.log"),
        "audit_tail": tail(OUT / "gold_3h_audit.log"),
    }
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
