# -*- coding: utf-8 -*-
"""在 Linux 上安装 NCBI BLAST+ 到 tools/blast+/bin，并构建 cDNA 库。不占 GPU。"""
from __future__ import annotations

import os
import shutil
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.entry import bootstrap_app

bootstrap_app()

from common_utils import safe_print

BLAST_FTP = "https://ftp.ncbi.nlm.nih.gov/blast/executables/blast+/LATEST/"
TOOLS_DIR = ROOT / "tools" / "blast+"
BIN_DIR = TOOLS_DIR / "bin"
FALLBACK = "https://ftp.ncbi.nlm.nih.gov/blast/executables/blast+/2.16.0/ncbi-blast-2.16.0+-x64-linux.tar.gz"


def _urlopen(url: str, timeout: int = 120):
    req = urllib.request.Request(url, headers={"User-Agent": "ZebrafishGeneQA/1.0"})
    return urllib.request.urlopen(req, timeout=timeout)


def _find_linux_url() -> str:
    try:
        with _urlopen(BLAST_FTP, timeout=60) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        names = []
        for part in html.split('"'):
            low = part.lower()
            if "linux" in low and "x64" in low and part.endswith(".tar.gz") and "src" not in low:
                names.append(part)
        if names:
            names.sort(reverse=True)
            return BLAST_FTP + names[0]
    except Exception as e:
        safe_print(f"  [提示] 解析 LATEST 失败，用固定版本：{e}")
    return FALLBACK


def install_linux_blast(force: bool = False) -> Path:
    blastn = BIN_DIR / "blastn"
    if blastn.is_file() and os.access(blastn, os.X_OK) and not force:
        safe_print(f"  [跳过] 已有 {blastn}")
        return BIN_DIR
    url = _find_linux_url()
    safe_print(f"  [下载] {url}")
    TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        archive = Path(td) / "blast.tgz"
        with _urlopen(url, timeout=300) as resp, archive.open("wb") as fh:
            shutil.copyfileobj(resp, fh)
        safe_print("  [解压]")
        with tarfile.open(archive, "r:gz") as tf:
            tf.extractall(td)
        found = None
        for root, _, files in os.walk(td):
            if "blastn" in files:
                found = Path(root)
                break
        if not found:
            raise SystemExit("archive 里没有 blastn")
        if BIN_DIR.exists():
            shutil.rmtree(BIN_DIR, ignore_errors=True)
        shutil.copytree(found, BIN_DIR)
    for name in ("blastn", "makeblastdb"):
        p = BIN_DIR / name
        if p.is_file():
            p.chmod(0o755)
    if not blastn.is_file():
        raise SystemExit(f"安装后仍无 {blastn}")
    safe_print(f"  [完成] {BIN_DIR}")
    return BIN_DIR


def main() -> int:
    force = "--force" in sys.argv
    rt = Path(os.environ.get("ZEBRA_RUNTIME", "/data/zoujin/deng/zebrafish_runtime"))
    db_dir = Path(os.environ.get("BLAST_DB_DIR", str(rt / "blast")))
    os.environ["BLAST_BIN"] = str(BIN_DIR)
    os.environ["BLAST_DB_DIR"] = str(db_dir)
    safe_print("=== NCBI BLAST+ Linux ===")
    install_linux_blast(force=force)
    from seq_toolkit import ensure_blast_database

    info = ensure_blast_database(force=force)
    if info.get("success"):
        safe_print(f"  索引：{info.get('database')} 来源：{info.get('source')}")
        return 0
    safe_print(f"  [失败] {info.get('error')}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
