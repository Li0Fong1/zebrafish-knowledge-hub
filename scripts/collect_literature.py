# -*- coding: utf-8 -*-
"""WoS + PubMed 广搜采集：凡提到 zebrafish 的文献均入库。"""
from core.entry import bootstrap_app

bootstrap_app()

import argparse
import json
import sys
import urllib.error

from common_utils import safe_print
from multi_literature_collector import run_multi_source_collection
from literature_collector import run_literature_collection
from literature_models import ProgressLoadError


def main():
    parser = argparse.ArgumentParser(
        description="斑马鱼文献采集 → RAG（Web of Science + PubMed，广搜 zebrafish）"
    )
    parser.add_argument(
        "--source",
        default="wos,pubmed",
        help="来源：wos | pubmed | wos,pubmed（默认两者都采）",
    )
    parser.add_argument(
        "--max-pubmed",
        type=int,
        default=0,
        help="PubMed 最多采集篇数，0=全部（默认 0，Europe PMC 约 17 万篇）",
    )
    parser.add_argument(
        "--wos-max",
        type=int,
        default=0,
        help="WoS API 每个检索式最多篇数，0=不限制",
    )
    parser.add_argument("--with-themes", action="store_true", help="额外跑细分主题检索（默认仅广搜）")
    parser.add_argument("--per-query", type=int, default=200, help="--with-themes 时每主题篇数")
    parser.add_argument("--gene-per-query", type=int, default=40, help="--with-themes 时基因专题篇数")
    parser.add_argument("--no-genes", action="store_true", help="--with-themes 时跳过基因专题")
    parser.add_argument("--no-wos-ris", action="store_true", help="不导入 data/wos_import/ 下 RIS 文件")
    parser.add_argument("--no-rebuild", action="store_true", help="入库后不重建 FAISS 索引")
    parser.add_argument("--no-resume", action="store_true", help="忽略进度，重新抓取")
    parser.add_argument(
        "--pubmed-backend",
        default="epmc",
        choices=("auto", "ncbi", "epmc"),
        help="PubMed 采集通道：epmc 镜像（默认，国内稳定）| auto | ncbi",
    )
    parser.add_argument("--pubmed-only", action="store_true", help="仅 PubMed（旧模式）")
    args = parser.parse_args()

    try:
        if args.pubmed_only:
            run_literature_collection(
                per_query=args.per_query,
                include_genes=not args.no_genes,
                rebuild_index=not args.no_rebuild,
                resume=not args.no_resume,
                gene_per_query=args.gene_per_query,
            )
            return

        sources = [s.strip() for s in args.source.split(",") if s.strip()]

        run_multi_source_collection(
            sources=sources,
            max_pubmed=args.max_pubmed,
            wos_per_query=args.wos_max,
            broad_only=not args.with_themes,
            per_query=args.per_query,
            include_genes=not args.no_genes,
            gene_per_query=args.gene_per_query,
            rebuild_index=not args.no_rebuild,
            resume=not args.no_resume,
            import_wos_ris=not args.no_wos_ris,
            pubmed_backend=args.pubmed_backend,
        )
    except KeyboardInterrupt:
        safe_print("\n[中断] 进度已保存，下次运行自动续传。")
        sys.exit(130)
    except ProgressLoadError as e:
        safe_print(f"[致命错误] {e}")
        sys.exit(2)
    except Exception as e:
        safe_print(f"[错误] {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
