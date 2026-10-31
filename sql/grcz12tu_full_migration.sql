-- GRCz12tu 全量基因位点表（RefSeq GCF_049306965.2）
-- 背景：gene_assembly_xref 有外键 gene_id -> gene_basic(gene_id)，只能装「能映射到本地主表」的基因，
--       因此 GRCz12tu 官方 5 万多条基因里只有约 2.2 万条进得去（覆盖率约 45%）。
--       本表不受该外键约束，用于承载 RefSeq 注释的**全量**基因，供标准名解析、ID 对照、坐标查询使用。
USE zebrafish_db;

CREATE TABLE IF NOT EXISTS grcz12tu_gene (
    id                  INT AUTO_INCREMENT PRIMARY KEY,
    ncbi_gene_id        VARCHAR(16)  NULL COMMENT 'NCBI GeneID（个别位点无 GeneID，留 NULL）',
    gene_symbol         VARCHAR(128) NULL COMMENT 'RefSeq 官方符号',
    locus_tag           VARCHAR(64)  NULL,
    gene_biotype        VARCHAR(64)  NULL COMMENT 'protein_coding / lncRNA / tRNA / pseudogene ...',
    chromosome          VARCHAR(16)  NULL COMMENT '1..25（.2 feature_table 无 MT 行）',
    genomic_accession   VARCHAR(32)  NULL COMMENT 'NC_1331xx.x',
    start_pos           BIGINT       NULL,
    end_pos             BIGINT       NULL,
    strand              CHAR(1)      NULL,
    assembly_unit       VARCHAR(32)  NULL COMMENT 'Primary Assembly',
    seq_type            VARCHAR(32)  NULL COMMENT 'chromosome',
    product_accession   VARCHAR(64)  NULL COMMENT '代表性 RefSeq 转录本/蛋白',
    gene_id             INT          NULL COMMENT '可映射到 gene_basic 时填入（故意不加外键，允许纯 RefSeq 基因）',
    source              VARCHAR(32)  NOT NULL DEFAULT 'refseq_GCF_049306965.2',
    updated_at          TIMESTAMP    DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    -- 唯一键按「基因片段」：NCBI 对跨 gap 的 split gene 会用同一 GeneID 给出 2-3 段，
    -- 只用 GeneID 做唯一键会把这些片段覆盖掉（少 154 行），因此把片段起点纳入唯一键。
    UNIQUE KEY uk_gene_segment (ncbi_gene_id, chromosome, start_pos),
    KEY idx_symbol (gene_symbol),
    KEY idx_biotype (gene_biotype),
    KEY idx_locus (chromosome, start_pos, end_pos),
    KEY idx_gene_id (gene_id),
    KEY idx_symbol_lower (gene_symbol, gene_biotype)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='GRCz12tu 全量基因位点（RefSeq GCF_049306965.2 feature_table，53,513 条）';
