# -*- coding: utf-8 -*-
"""
安装 NCBI BLAST+（Windows x64）并构建 cDNA 索引。

用法：python -m scripts.setup_blast
      python -m scripts.setup_blast --build-db-only
"""
from core.entry import bootstrap_app

bootstrap_app()

import argparse
import os
import shutil
import subprocess
import tarfile
import urllib.request
import zipfile

from common_utils import safe_print
from config import _BASE_DIR

BLAST_FTP = "https://ftp.ncbi.nlm.nih.gov/blast/executables/blast+/LATEST/"
TOOLS_DIR = os.path.join(_BASE_DIR, "tools", "blast+")


def _find_latest_win64_url() -> str:
    """解析 LATEST 目录中的 win64 包名。"""
    req = urllib.request.Request(BLAST_FTP, headers={"User-Agent": "ZebrafishGeneQA/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        html = resp.read().decode("utf-8", errors="replace")
    for name in sorted(html.split('"'), reverse=True):
        if "win64" in name.lower() and (name.endswith(".zip") or name.endswith(".tar.gz")):
            return BLAST_FTP + name
    return BLAST_FTP + "ncbi-blast-2.16.0+-win64.zip"


def install_blast_plus(force: bool = False) -> bool:
    bin_dir = os.path.join(TOOLS_DIR, "bin")
    blastn = os.path.join(bin_dir, "blastn.exe" if os.name == "nt" else "blastn")
    if os.path.isfile(blastn) and not force:
        safe_print(f"  [跳过] BLAST+ 已存在：{blastn}")
        return True

    url = _find_latest_win64_url()
    safe_print(f"  [下载] {url}")
    os.makedirs(TOOLS_DIR, exist_ok=True)
    archive = os.path.join(TOOLS_DIR, os.path.basename(url.split("?")[0]))
    urllib.request.urlretrieve(url, archive)

    safe_print("  [解压] …")
    if archive.endswith(".zip"):
        with zipfile.ZipFile(archive, "r") as zf:
            zf.extractall(TOOLS_DIR)
    else:
        with tarfile.open(archive, "r:*") as tf:
            tf.extractall(TOOLS_DIR)

    for root, _, files in os.walk(TOOLS_DIR):
        if "blastn.exe" in files or "blastn" in files:
            src_bin = root
            if src_bin != bin_dir:
                if os.path.isdir(bin_dir):
                    shutil.rmtree(bin_dir, ignore_errors=True)
                os.makedirs(os.path.dirname(bin_dir), exist_ok=True)
                shutil.move(src_bin, bin_dir)
            break

    safe_print(f"  [完成] BLAST+ → {bin_dir}")
    safe_print(f"  可将 BLAST_BIN 设为：{bin_dir}")
    return os.path.isfile(blastn) or os.path.isfile(blastn + ".exe")


def main():
    p = argparse.ArgumentParser(description="安装 BLAST+ 并构建 cDNA 索引")
    p.add_argument("--force", action="store_true", help="重新下载 BLAST+")
    p.add_argument("--build-db-only", action="store_true", help="仅构建索引（需已安装 BLAST+）")
    args = p.parse_args()

    if not args.build_db_only:
        safe_print("=== 安装 NCBI BLAST+ ===")
        if not install_blast_plus(force=args.force):
            safe_print("[失败] 未找到 blastn，请手动安装并设置 BLAST_BIN")
            return

    os.environ.setdefault("BLAST_BIN", os.path.join(TOOLS_DIR, "bin"))
    safe_print("\n=== 构建 cDNA BLAST 索引 ===")
    from seq_toolkit import ensure_blast_database
    info = ensure_blast_database(force=args.force)
    if info.get("success"):
        safe_print(f"  索引：{info['database']}")
        safe_print(f"  来源：{info['source']}")
    else:
        safe_print(f"  [失败] {info.get('error')}")


if __name__ == "__main__":
    main()
