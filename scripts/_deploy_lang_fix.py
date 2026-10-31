# -*- coding: utf-8 -*-
"""把「点名语言优先 + 繁转简兜底」打到线上（只做最小补丁，不覆盖服务器独有的功能）。"""
import os
import tempfile
import time
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parents[1]
env = {}
for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
    if "=" in line and not line.strip().startswith("#"):
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()

BASE = "/data/zoujin/deng/zebrafish_gene_qa"
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(env["ZF_HOST"], port=int(env["ZF_PORT"]), username=env["ZF_USER"],
          password=env["ZF_PASS"], timeout=20)
sf = c.open_sftp()


def sh(cmd, timeout=60):
    _i, o, e = c.exec_command(cmd, timeout=timeout)
    out = o.read().decode("utf-8", "replace")
    err = e.read().decode("utf-8", "replace")
    return out, err


# 1) prompt：本地新版 + 给服务器独有的 answer_is_english 补一个兼容壳
prompt = (ROOT / "llm" / "zebrafish_prompt.py").read_text(encoding="utf-8")
if "def answer_is_english" not in prompt:
    prompt += '''


# ── 兼容壳（服务器 api_server.py 仍在用）：回答是否应为英文 ──────────────
def answer_is_english(question: str = "") -> bool:
    return current_answer_language(question) == "en"
'''
prompt_new = "/tmp/zebrafish_prompt.py.new"
with sf.open(prompt_new, "w") as f:
    f.write(prompt)

# 2) api_server：最小字符串补丁
remote_api = os.path.join(tempfile.gettempdir(), "api_server.py.cur")
sf.get(BASE + "/api/api_server.py", remote_api)
src = Path(remote_api).read_text(encoding="utf-8")
orig = src

reps = []

reps.append((
    "def _apply_answer_language(lang: Optional[str]) -> None:",
    "def _apply_answer_language(lang: Optional[str], question: str = \"\") -> None:",
))
reps.append((
    "        from zebrafish_prompt import set_answer_language\n\n        set_answer_language(lang)",
    "        from zebrafish_prompt import (\n"
    "            detect_explicit_language_request,\n"
    "            detect_question_language,\n"
    "            set_answer_language,\n"
    "        )\n\n"
    "        effective = (\n"
    "            detect_explicit_language_request(question)\n"
    "            or (lang or \"\").strip().lower()[:2]\n"
    "            or detect_question_language(question)\n"
    "        )\n"
    "        set_answer_language(effective)",
))
reps.append((
    '_apply_answer_language(getattr(req, "lang", None))',
    '_apply_answer_language(getattr(req, "lang", None), question)',
))
reps.append((
    "        try:\n            q.put_nowait((\"delta\", text))",
    "        try:\n"
    "            try:\n"
    "                from zebrafish_prompt import simplify_answer_text\n\n"
    "                text = simplify_answer_text(text)\n"
    "            except Exception:\n"
    "                pass\n"
    "            q.put_nowait((\"delta\", text))",
))
anchor_tail = (
    "            result[\"answer\"] = strip_unverified_design_links(\n"
    "                str(result.get(\"answer\") or \"\"),\n"
    "                result,\n"
    "            )\n"
    "        except Exception:\n"
    "            pass\n"
    "    return result"
)
tail_new = (
    "            result[\"answer\"] = strip_unverified_design_links(\n"
    "                str(result.get(\"answer\") or \"\"),\n"
    "                result,\n"
    "            )\n"
    "        except Exception:\n"
    "            pass\n"
    "    # 中文回答出口统一转简体（英文回答 / 基因符号 / ID 不受影响）\n"
    "    try:\n"
    "        if isinstance(result, dict) and isinstance(result.get(\"answer\"), str):\n"
    "            from zebrafish_prompt import simplify_answer_text\n\n"
    "            result = {**result, \"answer\": simplify_answer_text(result[\"answer\"])}\n"
    "    except Exception as e:  # noqa: BLE001\n"
    "        logger.warning(f\"[语言] 繁体转简体失败（返回原文）：{e}\")\n"
    "    return result"
)
reps.append((anchor_tail, tail_new))

for old, new in reps:
    n = src.count(old)
    if n != 1:
        raise SystemExit(f"补丁锚点命中 {n} 次（应为 1）：{old[:60]!r}")
    src = src.replace(old, new)

print("api_server 补丁 OK，长度", len(orig), "->", len(src))
api_new = "/tmp/api_server.py.new"
with sf.open(api_new, "w") as f:
    f.write(src)

# 3) 远端语法检查（放到用户可写的临时目录，避免 /tmp/__pycache__ 权限问题）
tmpdir = BASE + "/.tmp_langfix"
sh(f"mkdir -p {tmpdir}")
with sf.open(tmpdir + "/zebrafish_prompt.py", "w") as f:
    f.write(prompt)
with sf.open(tmpdir + "/api_server.py", "w") as f:
    f.write(src)
py = BASE + "/.venv/bin/python"
out, err = sh(
    f"cd {BASE} && PYTHONPYCACHEPREFIX={tmpdir}/pycache {py} -m py_compile "
    f"{tmpdir}/zebrafish_prompt.py {tmpdir}/api_server.py && echo COMPILE_OK"
)
print(out.strip(), err.strip()[:300])
if "COMPILE_OK" not in out:
    raise SystemExit("远端 py_compile 失败，未落盘")

# 4) 备份 + 落盘
ts = time.strftime("%Y%m%d-%H%M%S")
cmds = [
    f"cp {BASE}/llm/zebrafish_prompt.py {BASE}/llm/zebrafish_prompt.py.bak-{ts}",
    f"cp {BASE}/api/api_server.py {BASE}/api/api_server.py.bak-{ts}",
    f"cp {tmpdir}/zebrafish_prompt.py {BASE}/llm/zebrafish_prompt.py",
    f"cp {tmpdir}/api_server.py {BASE}/api/api_server.py",
    f"cd {BASE} && PYTHONPYCACHEPREFIX={tmpdir}/pycache {py} -m py_compile llm/zebrafish_prompt.py api/api_server.py && echo DEPLOY_COMPILE_OK",
]
for cmd in cmds:
    out, err = sh(cmd, timeout=120)
    print("$", cmd.split("&&")[-1].strip(), "->", out.strip()[:120], err.strip()[:200])

sh(f"rm -rf {tmpdir}")
print("BACKUP_TS", ts)
