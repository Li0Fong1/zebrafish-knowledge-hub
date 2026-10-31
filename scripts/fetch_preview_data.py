# -*- coding: utf-8 -*-
"""把服务器上的真实数据抓成本地预览快照（不造假数字）。

产出（均在 scripts/preview_data/）：
    hub_recent_papers.json   ← 服务器 $RT/hub_recent_papers.json（每日论文候选池，200 条）
    research_feed_db.json    ← MySQL 真实查询：论文总数 / 年度分布 / 同步记录 + 维护状态

用途：本地预览服务（scripts/preview_web.py）据此实现 /api/hub/research-feed，
      离线也能看到与线上同源的首页「研究动态」内容。

用法：
    .venv\\Scripts\\python.exe -X utf8 scripts\\fetch_preview_data.py
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import paramiko  # noqa: E402

RT = "/data/zoujin/deng/zebrafish_runtime"
MYSQL = RT + "/mysql80/bin/mysql"
MYSQL_ARGS = "-h127.0.0.1 -P3307 -uzebrafish -pzebrafish_remote_2026 zebrafish_db -B -N"
try:  # 凭据只从 .env / 环境变量读，源码内不得硬编码（见 RELEASE_SECURITY_AUDIT）
    from _zf_creds import HOST, PORT, USER, require_password
except ImportError:  # 以包形式导入时（python -m scripts.xxx）
    from scripts._zf_creds import HOST, PORT, USER, require_password

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "scripts" / "preview_data"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    pwd = require_password()
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    cli.connect(HOST, port=PORT, username=USER, password=pwd, timeout=45,
                banner_timeout=60, auth_timeout=60, allow_agent=False, look_for_keys=False)
    cli.get_transport().set_keepalive(15)

    def run(cmd: str, timeout: int = 120) -> str:
        ch = cli.get_transport().open_session()
        ch.settimeout(timeout)
        ch.exec_command(cmd)
        out = b""
        while True:
            try:
                buf = ch.recv(65536)
            except Exception:
                break
            if not buf:
                break
            out += buf
        ch.close()
        return out.decode("utf-8", "replace")

    ok = []
    warn = []

    # ---------------- 1) 每日论文候选池 ----------------
    try:
        sftp = cli.open_sftp()
        remote = RT + "/hub_recent_papers.json"
        local = OUT / "hub_recent_papers.json"
        sftp.get(remote, str(local))
        d = json.loads(local.read_text(encoding="utf-8"))
        n = len(d.get("papers") or [])
        ok.append("hub_recent_papers.json: %d 条候选（source=%s, generated_at=%s）"
                  % (n, d.get("source"), d.get("generated_at")))
        sftp.close()
    except Exception as e:  # noqa: BLE001
        warn.append("hub_recent_papers.json 拉取失败: %s: %s" % (type(e).__name__, e))

    # ---------------- 2) MySQL 真实查询 ----------------
    snap: dict = {"_fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                  "_source": "%s via %s" % (HOST, MYSQL_ARGS.split(" -p")[0])}

    def mysql(sql: str, timeout: int = 60) -> list[list[str]]:
        out = run('%s %s -e "%s"' % (MYSQL, MYSQL_ARGS, sql.replace('"', '\\"')), timeout)
        rows = []
        for line in out.splitlines():
            if line.strip() == "" or "Warning" in line:
                continue
            rows.append(line.split("\t"))
        return rows

    try:
        rows = mysql("SELECT COUNT(*) FROM literature_paper")
        if rows and rows[0] and rows[0][0].isdigit():
            snap["papers_total"] = int(rows[0][0])
            ok.append("literature_paper 总数: %s" % snap["papers_total"])
    except Exception as e:  # noqa: BLE001
        warn.append("papers 总数查询失败: %s" % e)

    try:
        rows = mysql("SELECT year, COUNT(*) AS n FROM literature_paper "
                     "WHERE year IS NOT NULL AND year<>'' GROUP BY year "
                     "ORDER BY year DESC LIMIT 6")
        snap["years"] = [{"year": r[0], "count": int(r[1])} for r in rows if len(r) >= 2]
        ok.append("年度分布: %s" % ", ".join("%s=%s" % (y["year"], y["count"]) for y in snap["years"]))
    except Exception as e:  # noqa: BLE001
        warn.append("年度分布查询失败: %s" % e)

    try:
        rows = mysql("SELECT event_time, actor, action, IFNULL(index_version,''), "
                     "LEFT(IFNULL(detail_json,''),200) FROM literature_sync_log "
                     "ORDER BY id DESC LIMIT 6")
        snap["updates"] = [
            {"event_time": r[0], "actor": r[1] if len(r) > 1 else "",
             "action": r[2] if len(r) > 2 else "",
             "index_version": r[3] if len(r) > 3 else "",
             "detail": r[4] if len(r) > 4 else ""}
            for r in rows if r
        ]
        ok.append("同步记录: %d 条" % len(snap["updates"]))
    except Exception as e:  # noqa: BLE001
        warn.append("同步记录查询失败: %s" % e)

    # ---------------- 3) 维护状态（状态文件 + 磁盘产物） ----------------
    try:
        raw = run("for f in " + RT + "/*maintenance*.json " + RT + "/*maint*.json; do "
                  "test -f \"$f\" && echo \"###FILE:$f\" && cat \"$f\"; done", 60)
        for blk in raw.split("###FILE:")[1:]:
            head, _, body = blk.partition("\n")
            try:
                snap.setdefault("maintenance_state", {})[os.path.basename(head.strip())] = json.loads(body)
            except Exception:
                pass
        # 状态文件通常是 state 段
        st = snap.get("maintenance_state", {})
        for name, val in st.items():
            if isinstance(val, dict) and "literature_last_ts" in val:
                snap["maintenance"] = val
                break
        if snap.get("maintenance"):
            ok.append("维护状态: literature_last_ts=%s" % snap["maintenance"].get("literature_last_ts"))
        else:
            warn.append("维护状态未找到（预览会用静态演示值）")
    except Exception as e:  # noqa: BLE001
        warn.append("维护状态读取失败: %s" % e)

    # ---------------- 4) 基因组总览（长度/GC 缓存 + 本地注释基因数 + BLAST 库规模） ----------------
    try:
        sftp = cli.open_sftp()
        sftp.get(RT + "/genome_info.json", str(OUT / "genome_info.json"))
        sftp.close()
        ok.append("genome_info.json 已下载")
    except Exception as e:  # noqa: BLE001
        warn.append("genome_info.json 拉取失败: %s" % e)

    genome: dict = {"_fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    try:
        rows = mysql("SELECT chromosome, COUNT(*) FROM gene_basic GROUP BY chromosome")
        per, other, total = {}, 0, 0
        for r in rows:
            if len(r) < 2:
                continue
            chrom, cnt = r[0].strip(), int(r[1])
            total += cnt
            if chrom.isdigit():
                per[chrom] = per.get(chrom, 0) + cnt
            else:
                other += cnt
        genome.update({"chrom_counts": per, "other": other, "total": total})
        ok.append("按染色体基因数: %d 条编号染色体 / 合计 %d" % (len(per), total))
    except Exception as e:  # noqa: BLE001
        warn.append("按染色体基因数查询失败: %s" % e)

    try:
        rows = mysql(
            "SELECT JSON_OBJECT('assembly_name',assembly_name,"
            "'ncbi_refseq_accession',ncbi_refseq_accession,"
            "'ncbi_genbank_accession',ncbi_genbank_accession,'strain',strain,"
            "'is_local_anchor',is_local_anchor,'is_ncbi_reference',is_ncbi_reference,"
            "'ensembl_assembly',ensembl_assembly,'ensembl_release',ensembl_release,"
            "'notes',notes) FROM assembly_registry "
            "ORDER BY is_local_anchor DESC, assembly_name")
        genome["assemblies"] = [json.loads(r[0]) for r in rows if r and r[0].startswith("{")]
        ok.append("assembly_registry: %d 行" % len(genome["assemblies"]))
    except Exception as e:  # noqa: BLE001
        warn.append("assembly_registry 查询失败: %s" % e)

    try:
        raw = run("for f in " + RT + "/blast/cdna_db.nsq " + RT + "/blast/genome_db.nsq "
                  + RT + "/blast/grcz12tu_cds.fna " + RT + "/blast/grcz12tu_genome.fna; do "
                  "test -f \"$f\" && echo \"$(basename $f) $(stat -c %s $f)\"; done", 60)
        libs = {}
        for line in raw.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1].isdigit():
                libs[parts[0]] = int(parts[1])
        genome["lib_files"] = libs
        ok.append("BLAST 库文件: %s" % ", ".join("%s=%.1fMB" % (k, v / 1048576) for k, v in libs.items()))
    except Exception as e:  # noqa: BLE001
        warn.append("BLAST 库规模读取失败: %s" % e)

    try:
        rows = mysql(
            "SELECT chromosome, JSON_OBJECT('gene_id',gene_id,'gene_name',gene_name,"
            "'gene_symbol',gene_symbol,'ensembl_id',ensembl_id,'zfin_id',zfin_id,"
            "'gene_function',gene_function) FROM ("
            "  SELECT *, ROW_NUMBER() OVER (PARTITION BY chromosome ORDER BY gene_name) rn"
            "  FROM gene_basic WHERE chromosome REGEXP '^[0-9]+$'"
            ") t WHERE rn <= 60 ORDER BY chromosome, gene_name", timeout=180)
        genes: dict = {}
        for r in rows:
            if len(r) < 2 or not r[1].startswith("{"):
                continue
            genes.setdefault(r[0].strip(), []).append(json.loads(r[1]))
        genome["chrom_genes"] = genes
        ok.append("染色体基因示例: %d 条染色体 × %d 条"
                  % (len(genes), len(next(iter(genes.values()))) if genes else 0))
    except Exception as e:  # noqa: BLE001
        warn.append("染色体基因示例查询失败: %s" % e)

    (OUT / "genome_preview.json").write_text(
        json.dumps(genome, ensure_ascii=False, indent=2), encoding="utf-8")

    (OUT / "research_feed_db.json").write_text(
        json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=== 成功 ===")
    for line in ok:
        print("  ✓", line)
    if warn:
        print("=== 警告 ===")
        for line in warn:
            print("  !", line)
    print("\n产物目录:", OUT)
    cli.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
