# -*- coding: utf-8 -*-
"""测试 NCBI E-utilities API Key 是否可用。"""
from core.entry import bootstrap_app

bootstrap_app()

import json
import os
import urllib.error
import urllib.parse
import urllib.request

from common_utils import safe_print

NCBI_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


def _try_search(use_key: bool) -> tuple[bool, str]:
    email = os.getenv("NCBI_EMAIL", "zebrafish-gene-qa@local.dev")
    params = {
        "db": "pubmed",
        "term": "zebrafish",
        "retmax": 1,
        "retmode": "json",
        "email": email,
        "tool": "ZebrafishGeneQA",
    }
    if use_key:
        key = os.getenv("NCBI_API_KEY", "").strip()
        if not key:
            return False, "未设置 NCBI_API_KEY"
        params["api_key"] = key
    url = NCBI_BASE + "/esearch.fcgi?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(
            urllib.request.Request(url, headers={"User-Agent": "ZebrafishGeneQA/1.0"}),
            timeout=30,
        ) as resp:
            data = json.loads(resp.read())
            count = data.get("esearchresult", {}).get("count", "?")
            return True, f"OK，检索到 {count} 篇"
    except urllib.error.HTTPError as e:
        return False, e.read().decode("utf-8", errors="replace")[:300]


def main():
    safe_print("【NCBI API Key 检测】")
    safe_print(f"  NCBI_EMAIL: {os.getenv('NCBI_EMAIL', '(未设置)')}")
    key = os.getenv("NCBI_API_KEY", "").strip()
    if key:
        safe_print(f"  NCBI_API_KEY: {key[:6]}...{key[-4:]} ({len(key)} 字符)")
    else:
        safe_print("  NCBI_API_KEY: (未设置)")

    ok0, msg0 = _try_search(use_key=False)
    safe_print(f"\n无 Key 测试: {'通过' if ok0 else '失败'} — {msg0}")

    if key:
        ok1, msg1 = _try_search(use_key=True)
        safe_print(f"有 Key 测试: {'通过' if ok1 else '失败'} — {msg1}")
        if not ok1:
            safe_print("\n建议：")
            safe_print("  1. 打开 NCBI → Account settings → API Key Management")
            safe_print("  2. 点「Replace」生成新 Key（旧 Key 会立即失效）")
            safe_print("  3. 完整复制新 Key 到 .env 的 NCBI_API_KEY=...")
            safe_print("  4. 再运行: python -m scripts.test_ncbi_key")
            safe_print("\n文献采集仍可用无 Key 模式（约 3 req/s），菜单 11 会自动降级。")


if __name__ == "__main__":
    main()
