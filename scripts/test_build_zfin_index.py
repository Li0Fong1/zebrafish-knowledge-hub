# -*- coding: utf-8 -*-
"""隔离冒烟测试：验证 ZFIN 表型索引的「构建 / 查询 / 未构建守卫」代码路径。

不依赖真实 ZFIN 数据、不污染本机：把 BLAST_DB_DIR 指到临时目录，写入合成
phenoGeneCleanData_fish.txt，跑 build_index，再验证 schema / 5 个索引 / 查询，
最后清空临时目录。
"""
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_tmp = tempfile.mkdtemp(prefix="zfin_smoke_")
# 必须在 import phenotype_toolkit 之前设定，因为 BLAST_DIR 在导入时求值
os.environ["BLAST_DB_DIR"] = os.path.join(_tmp, "blast")
sys.path[:0] = [ROOT, os.path.join(ROOT, "core"), os.path.join(ROOT, "database")]

# 25+ 列的合成 ZFIN clean 格式行（列序同 phenotype_toolkit 注释）
_ROW_TMPL = (
    "{pid}\ttp53\tZDB-GENE-990415-270\t\t\t\t\tZFA:0000022\theart\tPATO:0000460\t"
    "abnormal heart morphology\t\t\t\t\t\t\t\t\t\t\tfish1\tgeno1\tprim-5\tprim-25\t\t"
    "ZDB-PUB-0001\tZDB-FIG-0001\n"
)


def _write_source():
    zfin_dir = os.path.join(_tmp, "zfin")
    os.makedirs(zfin_dir, exist_ok=True)
    path = os.path.join(zfin_dir, "phenoGeneCleanData_fish.txt")
    # 12 行（>1024 字节门槛，且更贴近真实体量）；全部符号 tp53 便于聚合断言
    with open(path, "w", encoding="utf-8") as fh:
        for i in range(12):
            fh.write(_ROW_TMPL.format(pid="PH%03d" % i))


def _main() -> int:
    failures = []
    try:
        import phenotype_toolkit as pt  # noqa: E402

        _write_source()
        assert pt._source_files(), "源文件未被识别"

        # 1) 未构建守卫：删除索引后再查应返回 ready=False
        if os.path.isfile(pt._sqlite_path()):
            os.remove(pt._sqlite_path())
        g0 = pt.gene_phenotypes("tp53")
        assert g0.get("ready") is False, "未构建时应 ready=False"
        t0 = pt.term_search("heart")
        assert t0.get("ready") is False, "未构建时 term_search 应 ready=False"
        print("  PASS  未构建守卫（ready=False）")

        # 2) 构建
        res = pt.build_index(force=True)
        assert res.get("ok"), "build_index 失败: %s" % res
        assert res["rows"] == 12, "行数应为 12，实际 %s" % res.get("rows")
        print("  PASS  build_index 成功（rows=%d）" % res["rows"])

        # 3) 5 个索引已创建
        with pt._conn() as conn:
            idx = {r["name"] for r in conn.execute("PRAGMA index_list(pheno)")}
        expect = {"idx_pheno_symbol", "idx_pheno_gene", "idx_pheno_ao",
                  "idx_pheno_pato", "idx_pheno_pub"}
        assert expect <= idx, "缺少索引: %s" % (expect - idx)
        print("  PASS  5 个索引已创建: %s" % sorted(idx))

        # 4) 查询可用（gene_phenotypes 需先经 MySQL 解析基因；本机无 MySQL，用假客户端隔离）
        import mysql_client as _mclient  # gene_phenotypes 内部 `from mysql_client import`

        class _FakeMySQL:
            def execute_sql(self, sql, max_rows=10, params=None):
                return {"success": True, "data": [], "count": 0}

            def get_db(self):
                class _C:
                    def __enter__(self):
                        return self

                    def __exit__(self, *a):
                        return False
                return _C()

        _orig = _mclient.get_mysql_client
        _mclient.get_mysql_client = lambda: _FakeMySQL()  # 隔离真实的 MySQL 依赖
        try:
            g = pt.gene_phenotypes("tp53")
            assert g.get("ok") and g["n_rows"] == 12, "gene_phenotypes 应返回 12 行: %s" % g
            assert g.get("matched_symbol") == "tp53", "应匹配符号 tp53: %s" % g.get("matched_symbol")
            print("  PASS  gene_phenotypes 命中 12 行，匹配符号 tp53")
        finally:
            _mclient.get_mysql_client = _orig

        t = pt.term_search("heart")
        assert t.get("ok") and t["terms"], "term_search('heart') 应有结果: %s" % t
        print("  PASS  term_search('heart') 命中 %d 个术语" % len(t["terms"]))

        # 5) 幂等：再次增量构建应 skipped
        res2 = pt.build_index(force=False)
        assert res2.get("skipped"), "已建且新应 skipped: %s" % res2
        print("  PASS  增量构建幂等跳过（skipped=True）")
    except AssertionError as e:
        failures.append(str(e))
        print("  FAIL  %s" % e)
    finally:
        shutil.rmtree(_tmp, ignore_errors=True)

    print()
    if failures:
        print("结果：%d 项失败 -> %s" % (len(failures), failures))
        return 1
    print("结果：全部通过 ✅（构建逻辑可用，仅差服务器侧源数据）")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
