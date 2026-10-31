# -*- coding: utf-8 -*-
"""数据库 schema 增量迁移（gene_reference 扩展 ref 键长度）。"""
import bootstrap  # noqa: F401

from sqlalchemy import text

from common_utils import logger, safe_print
from mysql_client import get_mysql_client


def migrate_gene_reference_ref_key() -> bool:
    client = get_mysql_client()
    db = next(client.get_db())
    try:
        row = db.execute(text(
            "SELECT CHARACTER_MAXIMUM_LENGTH FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'gene_reference' "
            "AND COLUMN_NAME = 'pubmed_id'"
        )).mappings().first()
        cur_len = int(row["CHARACTER_MAXIMUM_LENGTH"] or 16) if row else 16
        if cur_len < 64:
            db.execute(text(
                "ALTER TABLE gene_reference MODIFY pubmed_id VARCHAR(64) NULL "
                "COMMENT 'PMID 或 WOS:/DOI: 引用键'"
            ))
            db.commit()
            safe_print("  [migrate] gene_reference.pubmed_id → VARCHAR(64)")
        else:
            safe_print("  [migrate] gene_reference.pubmed_id 已是 VARCHAR(64+)")
        return True
    except Exception as e:
        db.rollback()
        logger.error(f"[migrate] 失败: {e}")
        return False
    finally:
        db.close()


if __name__ == "__main__":
    migrate_gene_reference_ref_key()
