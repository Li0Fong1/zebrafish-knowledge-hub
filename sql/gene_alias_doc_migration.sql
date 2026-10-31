-- 别名 ↔ 文献 全文级倒排（任务① 扩展：从题名级升级到摘要/全文级）
--
-- 背景（2026-10-16 实测）：literature_paper 只有题名，没有摘要列；
--   literature_fulltext_gold 有 32,549 行但 body_text 全为空 —— 正文按设计**外置磁盘**，
--   真身在 解析文件/yes_fulltext_gold/batch_*/papers/*.json（含 title / sections.abstract / body_text）。
--   所以"全文级"别名证据必须**离线扫一遍语料落表**，查询时才能秒回，不能靠 SQL LIKE。
--
-- 两张表分工：
--   gene_alias_lit_stat —— 精确计数（每个别名在题名/摘要/全文中各命中多少篇），一行一个别名，很小；
--   gene_alias_doc      —— 明细样本（每别名最多留 N 篇，带命中片段），供回答里给引用与核对。
USE zebrafish_db;

-- 别名 × 文献 的命中统计（精确，不受样本截断影响）
CREATE TABLE IF NOT EXISTS gene_alias_lit_stat (
    id            BIGINT AUTO_INCREMENT PRIMARY KEY,
    gene_name     VARCHAR(64)  NOT NULL COMMENT '标准符号/locus',
    alias         VARCHAR(191) NOT NULL COMMENT '别名原文',
    alias_key     VARCHAR(191) NOT NULL COMMENT '别名小写，唯一键用',
    alias_source  VARCHAR(32)  NULL COMMENT 'zfin|ncbi|uniprot|ensembl|other|standard',
    title_docs    INT NOT NULL DEFAULT 0 COMMENT '题名命中篇数',
    abstract_docs INT NOT NULL DEFAULT 0 COMMENT '摘要命中篇数',
    body_docs     INT NOT NULL DEFAULT 0 COMMENT '全文中命中篇数',
    total_docs    INT NOT NULL DEFAULT 0 COMMENT '去重后的总篇数（三者并集）',
    scanned_docs  INT NOT NULL DEFAULT 0 COMMENT '本次扫描覆盖的文献数',
    built_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_gene_alias (gene_name, alias_key),
    KEY idx_gene (gene_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='别名↔文献命中统计（全文级）';

-- 明细样本：每个别名保留最值得引用的若干篇，带命中片段
CREATE TABLE IF NOT EXISTS gene_alias_doc (
    id         BIGINT AUTO_INCREMENT PRIMARY KEY,
    gene_name  VARCHAR(64)  NOT NULL,
    alias      VARCHAR(191) NOT NULL,
    alias_key  VARCHAR(191) NOT NULL,
    doc_id     VARCHAR(64)  NOT NULL COMMENT 'literature_paper.doc_id（pmid:x / eno:x）',
    pmid       VARCHAR(16)  NULL,
    doi        VARCHAR(128) NULL,
    field      VARCHAR(16)  NOT NULL COMMENT 'title|abstract|body（该篇里命中的最强字段）',
    hits       INT NOT NULL DEFAULT 0 COMMENT '该别名在该篇的命中次数',
    snippet    VARCHAR(600) NULL COMMENT '命中处上下文，供人核对',
    year       SMALLINT     NULL,
    journal    VARCHAR(512) NULL,
    built_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_alias_doc (alias_key, doc_id),
    KEY idx_gene (gene_name),
    KEY idx_alias (alias_key),
    KEY idx_doc (doc_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='别名↔文献明细样本（全文级）';
