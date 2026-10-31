# -*- coding: utf-8 -*-
"""给线上首页加 no-cache 头（避免用户改了版本号浏览器仍吃旧 HTML），只做最小字符串补丁。"""
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


def sh(cmd, timeout=90):
    _i, o, e = c.exec_command(cmd, timeout=timeout)
    return o.read().decode("utf-8", "replace"), e.read().decode("utf-8", "replace")


remote_api = os.path.join(tempfile.gettempdir(), "api_server.py.cur")
sf.get(BASE + "/api/api_server.py", remote_api)
src = Path(remote_api).read_text(encoding="utf-8")

OLD = '''@app.get("/")
def index():
    index_path = os.path.join(WEB_DIR, "index.html")
    if os.path.isfile(index_path):
        return FileResponse(index_path)'''
NEW = '''@app.get("/")
def index():
    index_path = os.path.join(WEB_DIR, "index.html")
    if os.path.isfile(index_path):
        # 首页必须每次回源校验，否则改完 HTML 里的 ?v= 版本号用户也拿不到
        return FileResponse(index_path, headers={
            "Cache-Control": "no-cache, must-revalidate",
            "Pragma": "no-cache",
        })'''

n = src.count(OLD)
if n == 0 and "no-cache, must-revalidate" in src:
    print("线上已是 no-cache 版本，跳过")
    raise SystemExit(0)
if n != 1:
    raise SystemExit(f"锚点命中 {n} 次（应为 1）")
src = src.replace(OLD, NEW)

tmpdir = BASE + "/.tmp_cachefix"
sh(f"mkdir -p {tmpdir}")
with sf.open(tmpdir + "/api_server.py", "w") as f:
    f.write(src)
py = BASE + "/.venv/bin/python"
out, err = sh(
    f"cd {BASE} && PYTHONPYCACHEPREFIX={tmpdir}/pycache {py} -m py_compile "
    f"{tmpdir}/api_server.py && echo COMPILE_OK"
)
print("compile:", out.strip(), err.strip()[:300])
if "COMPILE_OK" not in out:
    sh(f"rm -rf {tmpdir}")
    raise SystemExit("远端 py_compile 失败，未落盘")

ts = time.strftime("%Y%m%d-%H%M%S")
for cmd in [
    f"cp {BASE}/api/api_server.py {BASE}/api/api_server.py.bak-{ts}",
    f"cp {tmpdir}/api_server.py {BASE}/api/api_server.py",
    f"cd {BASE} && PYTHONPYCACHEPREFIX={tmpdir}/pycache {py} -m py_compile api/api_server.py && echo DEPLOY_OK",
]:
    out, err = sh(cmd, timeout=120)
    print("$", cmd.split("&&")[-1].strip(), "->", out.strip()[:80], err.strip()[:200])

sh(f"rm -rf {tmpdir}")
print("BACKUP_TS", ts)

# 触发守护脚本重启 worker
out, err = sh('pkill -f "uvicorn api.api_server" ; sleep 1 ; echo KILLED')
print("restart:", out.strip(), err.strip()[:200])
c.close()
