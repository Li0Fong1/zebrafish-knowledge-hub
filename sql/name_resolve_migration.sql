-- 别名并集解析层（吸收 CONTENT_CHUNK_ALIAS_SCHEME 精华，留在 MySQL）
-- 主键仍为 gene_basic.gene_id；zfin_id 仅作 xref 展示
-- 用法：mysql … < sql/name_resolve_migration.sql
-- 灌数：python -m scripts.build_name_resolve_layer

USE zebrafish_db;

CREATE TABLE IF NOT EXISTS name_hit_unique (
    query_norm   VARCHAR(128) NOT NULL COMMENT 'lower(trim) 查询串',
    gene_id      INT          NOT NULL,
    match_kind   VARCHAR(32)  NOT NULL COMMENT 'primary_symbol/alias_zfin/alias_ncbi/…',
    priority     INT          NOT NULL COMMENT '数字越小越优先',
    source_mask  INT          NOT NULL DEFAULT 0 COMMENT 'zfin=1 ncbi=2 uniprot=4 ensembl=8 xref=16',
    PRIMARY KEY (query_norm),
    KEY idx_nhu_gene (gene_id),
    CONSTRAINT fk_nhu_gene FOREIGN KEY (gene_id) REFERENCES gene_basic(gene_id) ON DELETE CASCADE
) ENGINE=InnoDB COMMENT='一词一基因：唯一命中表';

CREATE TABLE IF NOT EXISTS name_resolve_collision (
    query_norm          VARCHAR(128) NOT NULL,
    gene_id_count       INT          NOT NULL,
    candidate_gene_ids  JSON         NOT NULL COMMENT '按 gene_name 排序的 gene_id 列表',
    PRIMARY KEY (query_norm)
) ENGINE=InnoDB COMMENT='一词多基因：歧义表；禁止静默选第一名';

CREATE TABLE IF NOT EXISTS gene_name_bundle (
    gene_id            INT          NOT NULL PRIMARY KEY,
    zfin_id            VARCHAR(64)  NULL,
    primary_symbol     VARCHAR(128) NOT NULL COMMENT '本地库主符号 gene_basic.gene_name',
    standard_symbol    VARCHAR(128) NULL COMMENT 'GRCz12tu RefSeq 标准名（有则填）',
    aliases_zfin       JSON         NULL,
    aliases_ncbi       JSON         NULL,
    aliases_uniprot    JSON         NULL,
    aliases_ensembl    JSON         NULL,
    aliases_other      JSON         NULL,
    updated_at         TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_gnb_gene FOREIGN KEY (gene_id) REFERENCES gene_basic(gene_id) ON DELETE CASCADE
) ENGINE=InnoDB COMMENT='一基因全名包（物化，供 unique 命中后一次读出）';
