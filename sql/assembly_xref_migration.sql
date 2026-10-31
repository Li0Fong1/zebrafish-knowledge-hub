-- 参考基因组注册表 + 跨组装基因坐标/ID 映射（GRCz11 本地锚定 + GRCz12tu NCBI 参考）
USE zebrafish_db;

CREATE TABLE IF NOT EXISTS assembly_registry (
    assembly_name           VARCHAR(32)  NOT NULL PRIMARY KEY COMMENT '如 GRCz11 / GRCz12tu',
    ncbi_refseq_accession   VARCHAR(32)  NULL COMMENT 'GCF_*',
    ncbi_genbank_accession  VARCHAR(32)  NULL COMMENT 'GCA_*',
    strain                  VARCHAR(64)  NULL,
    is_local_anchor         TINYINT(1)   DEFAULT 0 COMMENT '1=本系统 gene_basic 锚定',
    is_ncbi_reference       TINYINT(1)   DEFAULT 0 COMMENT '1=NCBI Datasets 现行参考',
    ensembl_assembly        VARCHAR(32)  NULL,
    ensembl_release         VARCHAR(16)  NULL,
    notes                   TEXT         NULL,
    updated_at              TIMESTAMP    DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB COMMENT='参考基因组版本注册';

CREATE TABLE IF NOT EXISTS gene_assembly_xref (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    gene_id         INT NOT NULL,
    assembly_name   VARCHAR(32) NOT NULL,
    gene_symbol     VARCHAR(128) NULL,
    ncbi_gene_id    VARCHAR(16)  NULL,
    refseq_mrna     VARCHAR(32)  NULL,
    refseq_protein  VARCHAR(32)  NULL,
    ensembl_id      VARCHAR(32)  NULL COMMENT '该组装下的 Ensembl ID（若有）',
    chromosome      VARCHAR(16)  NULL,
    start_pos       BIGINT       NULL,
    end_pos         BIGINT       NULL,
    strand          CHAR(1)      NULL,
    source          VARCHAR(32)  NULL DEFAULT 'ncbi_gff',
    KEY idx_gene (gene_id),
    KEY idx_assembly_symbol (assembly_name, gene_symbol),
    KEY idx_ncbi_gene (ncbi_gene_id),
    UNIQUE KEY uk_gene_assembly (gene_id, assembly_name),
    CONSTRAINT fk_gasm_gene FOREIGN KEY (gene_id) REFERENCES gene_basic(gene_id) ON DELETE CASCADE
) ENGINE=InnoDB COMMENT='基因在各参考组装下的 ID/坐标';
