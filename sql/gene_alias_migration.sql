-- 基因别名与跨库 ID 映射表（增量迁移，不破坏现有数据）
-- 用法：mysql -u root -p zebrafish_db < sql/gene_alias_migration.sql

USE zebrafish_db;

CREATE TABLE IF NOT EXISTS gene_alias (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    gene_id     INT NOT NULL,
    alias       VARCHAR(128) NOT NULL COMMENT '别名/符号（小写存储便于检索）',
    alias_type  VARCHAR(32)  NULL COMMENT 'symbol/synonym/former/ncbi/other',
    source      VARCHAR(32)  NULL COMMENT 'ncbi/ensembl/zfin/manual/gene_basic',
    is_primary  TINYINT(1)   DEFAULT 0 COMMENT '1=官方主符号',
    KEY idx_alias (alias),
    KEY idx_gene (gene_id),
    UNIQUE KEY uk_alias_gene (alias, gene_id),
    CONSTRAINT fk_alias_gene FOREIGN KEY (gene_id) REFERENCES gene_basic(gene_id) ON DELETE CASCADE
) ENGINE=InnoDB COMMENT='基因符号别名索引';

CREATE TABLE IF NOT EXISTS gene_xref (
    id       INT AUTO_INCREMENT PRIMARY KEY,
    gene_id  INT NOT NULL,
    db_name  VARCHAR(32) NOT NULL COMMENT 'ncbi_gene/ensembl/zfin/refseq_mrna/refseq_protein/uniprot',
    db_id    VARCHAR(64) NOT NULL,
    db_label VARCHAR(128) NULL,
    source   VARCHAR(32)  NULL,
    KEY idx_gene (gene_id),
    KEY idx_db_id (db_name, db_id),
    UNIQUE KEY uk_gene_db (gene_id, db_name, db_id),
    CONSTRAINT fk_xref_gene FOREIGN KEY (gene_id) REFERENCES gene_basic(gene_id) ON DELETE CASCADE
) ENGINE=InnoDB COMMENT='跨数据库 ID 映射';
