# -*- coding: utf-8 -*-
"""离线自检：集中式 HTTP 模块 + nucleotide_accession_for_protein 重构。

不发起真实外网请求：用 monkeypatch 模拟 elink/esummary 的成功与失败，
确定性地验证「显式校验 / 缓存 / 审计 / NM_ 优先 / 优雅降级」。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "core"), os.path.join(ROOT, "database")]

import sequence_toolkit as st  # noqa: E402
from database import http_client  # noqa: E402

failures = []


def check(name, cond):
    if cond:
        print("  PASS  %s" % name)
    else:
        print("  FAIL  %s" % name)
        failures.append(name)


print("[1] 登录号格式校验")
check("NP_001258749.1 合法蛋白号", st._validate_protein_accession("NP_001258749.1"))
check("XP_123.2 合法蛋白号", st._validate_protein_accession("XP_123.2"))
check("YP_9 合法蛋白号", st._validate_protein_accession("YP_9"))
check("NM_001 非蛋白号(拒绝)", not st._validate_protein_accession("NM_001"))
check("garbage 拒绝", not st._validate_protein_accession("garbage"))
check("空串 拒绝", not st._validate_protein_accession(""))
check("NM_001271820.1 合法核酸号", st._validate_nucleotide_accession("NM_001271820.1"))
check("XM_0051.3 合法核酸号", st._validate_nucleotide_accession("XM_0051.3"))
check("NP_001 非核酸号(拒绝)", not st._validate_nucleotide_accession("NP_001"))

print("[2] 网络失败：降级返回空串 + 负缓存 + 审计留痕")
http_client._AUDIT.clear()
st._NT_ACC_CACHE.pop("NP_999999999.1", None)


class _FailOpener:
    def open(self, *a, **k):  # 模拟直连超时，触发真实 external_request 的失败审计分支
        import urllib.error
        raise urllib.error.URLError("simulated timeout")


_orig_opener = http_client._NO_PROXY_OPENER
http_client._NO_PROXY_OPENER = _FailOpener()
st.external_get_json = http_client.external_get_json  # 还原为真实实现
r = st.nucleotide_accession_for_protein("NP_999999999.1")
http_client._NO_PROXY_OPENER = _orig_opener  # 还原，避免影响后续用例
check("失败返回空串", r == "")
check("失败写入负缓存", st._NT_ACC_CACHE.get("NP_999999999.1") == "")
check("审计记录了失败(outcome 非 ok)",
      any(x["outcome"] in ("timeout", "network_error", "http_error")
          for x in http_client.http_audit_report()))

print("[3] 成功路径：NM_ 优先于 XM_")
st._NT_ACC_CACHE.pop("NP_001258749.1", None)


def _fake_ok(url, **k):
    if "elink" in url:
        return {"linksets": [{"linksetdbs": [{"links": ["111", "222"]}]}]}
    if "esummary" in url:
        return {"result": {
            "111": {"accessionversion": "XM_000111.1"},
            "222": {"accessionversion": "NM_001222.1"},
        }}
    return None


st.external_get_json = _fake_ok
r = st.nucleotide_accession_for_protein("NP_001258749.1")
check("返回 NM_ 而非 XM_", r == "NM_001222.1")
check("成功写入正缓存", st._NT_ACC_CACHE.get("NP_001258749.1") == "NM_001222.1")

print("[4] 命中核酸号但格式非法：兜底空串 + 负缓存")
st._NT_ACC_CACHE.pop("NP_555555555.1", None)


def _fake_bad(url, **k):
    if "elink" in url:
        return {"linksets": [{"linksetdbs": [{"links": ["333"]}]}]}
    if "esummary" in url:
        return {"result": {"333": {"accessionversion": "garbage"}}}
    return None


st.external_get_json = _fake_bad
r = st.nucleotide_accession_for_protein("NP_555555555.1")
check("非法核酸号返回空串", r == "")
check("非法核酸号负缓存", st._NT_ACC_CACHE.get("NP_555555555.1") == "")

print("[5] 缓存命中：不发第二次网络请求")
http_client._AUDIT.clear()
st.external_get_json = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("不应被调用"))
r = st.nucleotide_accession_for_protein("NP_001258749.1")  # 命中 [3] 的正缓存
check("缓存命中直接返回 NM_001222.1", r == "NM_001222.1")
check("缓存命中未触发新审计", len(http_client.http_audit_report()) == 0)

print()
if failures:
    print("结果：%d 项失败 -> %s" % (len(failures), failures))
    sys.exit(1)
print("结果：全部通过 ✅")
