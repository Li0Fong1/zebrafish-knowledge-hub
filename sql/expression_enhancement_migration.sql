-- 表达数据源 + Ensembl ID 版本映射（增量迁移）
-- 用法：mysql -u root -p zebrafish_db < sql/expression_enhancement_migration.sql

USE zebrafish_db;

-- gene_expression 增加数据来源字段（与胚胎 Atlas 数据并存）
-- 若列已存在，忽略 Duplicate column 错误即可
ALTER TABLE gene_expression
    ADD COLUMN data_source VARCHAR(32) DEFAULT 'atlas_embryo'
        COMMENT 'atlas_embryo=胚胎TPM / bgee=组织表达 / zfin=ZFIN';

UPDATE gene_expression SET data_source = 'atlas_embryo' WHERE data_source IS NULL;

-- Ensembl ID 版本映射（GRCz11 多 release 兼容）
CREATE TABLE IF NOT EXISTS ensembl_id_version (
    id               INT AUTO_INCREMENT PRIMARY KEY,
    gene_id          INT NOT NULL,
    ensembl_id       VARCHAR(32) NOT NULL COMMENT '含或不含 .version',
    id_base          VARCHAR(32) NOT NULL COMMENT '去版本号前缀',
    version_num      INT          NULL COMMENT '点号后版本号',
    ensembl_release  VARCHAR(16)  NULL,
    is_current       TINYINT(1)   DEFAULT 0,
    source           VARCHAR(32)  NULL COMMENT 'gene_basic/gene_xref/ensembl_rest/ncbi',
    KEY idx_eiv_base (id_base),
    KEY idx_eiv_ensembl (ensembl_id),
    KEY idx_eiv_gene (gene_id),
    UNIQUE KEY uk_eiv_gene_ensembl (gene_id, ensembl_id),
    CONSTRAINT fk_eiv_gene FOREIGN KEY (gene_id) REFERENCES gene_basic(gene_id) ON DELETE CASCADE
) ENGINE=InnoDB COMMENT='Ensembl ID 版本映射表';
