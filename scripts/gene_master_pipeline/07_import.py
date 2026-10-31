# -*- coding: utf-8 -*-
"""步骤 7：主表与映射证据表入库（MySQL）。"""
import io, os, sys, time

sys.path[:0] = ["/data/zoujin/deng/zebrafish_gene_qa"]
RT = "/data/zoujin/deng/zebrafish_runtime"
GM = RT + "/gene_map"
import pymysql


def load_tsv(path):
    with io.open(path, encoding="utf-8") as fh:
        hdr = fh.readline().rstrip("\n").split("\t")
        for line in fh:
            v = line.rstrip("\n").split("\t")
            if len(v) < len(hdr):
                v += [""] * (len(hdr) - len(v))
            yield dict(zip(hdr, v))


def main():
    env = {}
    for line in io.open("/data/zoujin/deng/zebrafish_gene_qa/.env", encoding="utf-8", errors="replace"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    cn = pymysql.connect(host=env.get("MYSQL_HOST", "localhost"), port=int(env.get("MYSQL_PORT", "3306")),
                         user=env.get("MYSQL_USER", "root"), password=env.get("MYSQL_PASSWORD", ""),
                         database=env.get("MYSQL_DATABASE", "zebrafish_db"), charset="utf8mb4")
    cur = cn.cursor()

    cur.execute("""CREATE TABLE IF NOT EXISTS gene_master_grcz12tu (
        grcz12tu_gene_id VARCHAR(32) NOT NULL PRIMARY KEY,
        symbol VARCHAR(128) DEFAULT '',
        biotype VARCHAR(64) DEFAULT '',
        chrom VARCHAR(32) DEFAULT '',
        start_pos BIGINT NULL,
        end_pos BIGINT NULL,
        strand VARCHAR(2) DEFAULT '',
        n_sources INT DEFAULT 0,
        source_gene_ids TEXT,
        ensembl_ids TEXT,
        zfin_ids TEXT,
        uniprot_ids TEXT,
        ncbi_gene_ids TEXT,
        aliases TEXT,
        n_go INT DEFAULT 0,
        n_kegg INT DEFAULT 0,
        has_func_desc TINYINT DEFAULT 0,
        is_protein_coding TINYINT DEFAULT 0,
        is_nonfunctional_type TINYINT DEFAULT 0,
        is_functional TINYINT DEFAULT 0,
        best_method VARCHAR(32) DEFAULT '',
        best_score VARCHAR(32) DEFAULT '',
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        KEY idx_symbol (symbol),
        KEY idx_biotype (biotype),
        KEY idx_functional (is_functional),
        KEY idx_ns (n_sources),
        KEY idx_chrom (chrom)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")

    cur.execute("""CREATE TABLE IF NOT EXISTS gene_id_map_grcz12tu (
        source_gene_id INT NOT NULL PRIMARY KEY,
        source_name VARCHAR(128) DEFAULT '',
        source_symbol VARCHAR(128) DEFAULT '',
        grcz12tu_gene_id VARCHAR(32) NOT NULL,
        grcz12tu_symbol VARCHAR(128) DEFAULT '',
        method VARCHAR(32) DEFAULT '',
        score VARCHAR(32) DEFAULT '',
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        KEY idx_target (grcz12tu_gene_id),
        KEY idx_method (method),
        KEY idx_name (source_name)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
    cn.commit()
    print("表结构就绪")

    # 主表
    rows = []
    for r in load_tsv(GM + "/gene_master_grcz12tu.tsv"):
        def i(k):
            v = r.get(k, "")
            try:
                return int(v)
            except Exception:
                return None if k in ("start_pos", "end_pos") else 0
        rows.append((r["grcz12tu_gene_id"], r["symbol"], r["biotype"], r["chrom"],
                     i("start"), i("end"), r["strand"], int(r["n_sources"] or 0),
                     r["source_gene_ids"], r["ensembl_ids"], r["zfin_ids"], r["uniprot_ids"],
                     r["ncbi_gene_ids"], r["aliases"], int(r["n_go"] or 0), int(r["n_kegg"] or 0),
                     int(r["has_func_desc"] or 0), int(r["is_protein_coding"] or 0),
                     int(r["is_nonfunctional_type"] or 0), int(r["is_functional"] or 0),
                     r["best_method"], r["best_score"]))
    cur.execute("TRUNCATE TABLE gene_master_grcz12tu")
    cur.executemany("""INSERT INTO gene_master_grcz12tu
        (grcz12tu_gene_id,symbol,biotype,chrom,start_pos,end_pos,strand,n_sources,source_gene_ids,
         ensembl_ids,zfin_ids,uniprot_ids,ncbi_gene_ids,aliases,n_go,n_kegg,has_func_desc,
         is_protein_coding,is_nonfunctional_type,is_functional,best_method,best_score)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", rows)
    cn.commit()
    print("主表导入 %d 行" % len(rows))

    # 证据表
    rows2 = []
    for r in load_tsv(GM + "/gene_id_map.tsv"):
        rows2.append((int(r["source_gene_id"]), r["source_name"], r["source_symbol"],
                      r["grcz12tu_gene_id"], r["grcz12tu_symbol"], r["method"], r["score"]))
    cur.execute("TRUNCATE TABLE gene_id_map_grcz12tu")
    cur.executemany("""INSERT INTO gene_id_map_grcz12tu
        (source_gene_id,source_name,source_symbol,grcz12tu_gene_id,grcz12tu_symbol,method,score)
        VALUES (%s,%s,%s,%s,%s,%s,%s)""", rows2)
    cn.commit()
    print("证据表导入 %d 行" % len(rows2))

    # 复核
    cur.execute("SELECT COUNT(*) FROM gene_master_grcz12tu")
    print("库中主表 %d 行" % cur.fetchone()[0])
    cur.execute("SELECT is_functional, COUNT(*) FROM gene_master_grcz12tu GROUP BY is_functional")
    print("功能标记分布:", cur.fetchall())
    cur.execute("SELECT method, COUNT(*) FROM gene_id_map_grcz12tu GROUP BY method ORDER BY 2 DESC")
    print("方法分布:", cur.fetchall())
    cur.execute("""SELECT m.grcz12tu_gene_id, m.symbol, m.biotype, m.chrom, m.start_pos, m.end_pos,
                          m.n_sources, m.best_method
                   FROM gene_master_grcz12tu m WHERE m.symbol IN ('tp53','sox9b','nanog','mitfa')""")
    print("抽查:", cur.fetchall())
    cn.close()


if __name__ == "__main__":
    main()
