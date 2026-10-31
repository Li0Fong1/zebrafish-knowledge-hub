-- ============================================================
-- 斑马鱼功能基因组数据库初始化脚本
-- 参考 Potato Knowledge Hub 架构：功能基因库 + 表达 + GO + 同源 + 文献
-- 用法：mysql -u root -p < sql/zebrafish_db_init.sql
--       或运行 init_database.bat / python init_database.py
-- ============================================================

CREATE DATABASE IF NOT EXISTS zebrafish_db
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_unicode_ci;

USE zebrafish_db;

DROP VIEW IF EXISTS gene;
DROP TABLE IF EXISTS gene_xref;
DROP TABLE IF EXISTS gene_alias;
DROP TABLE IF EXISTS gene_reference;
DROP TABLE IF EXISTS homology_gene;
DROP TABLE IF EXISTS gene_go;
DROP TABLE IF EXISTS gene_expression;
DROP TABLE IF EXISTS gene_basic;

-- ---------- 基因基础信息表 ----------
CREATE TABLE gene_basic (
    gene_id          INT AUTO_INCREMENT PRIMARY KEY,
    gene_name        VARCHAR(64)  NOT NULL COMMENT '基因符号',
    gene_symbol      VARCHAR(64)  NULL COMMENT '常用别名',
    ensembl_id       VARCHAR(32)  NULL COMMENT 'Ensembl ID',
    zfin_id          VARCHAR(32)  NULL COMMENT 'ZFIN ID',
    chromosome       VARCHAR(16)  NULL COMMENT '染色体',
    gene_function    TEXT         NULL COMMENT '功能描述',
    interpro_domain  VARCHAR(256) NULL COMMENT 'InterPro结构域',
    uniprot_id       VARCHAR(32)  NULL COMMENT 'UniProt ID',
    expression_summary VARCHAR(512) NULL COMMENT '表达概况',
    UNIQUE KEY uk_gene_name (gene_name),
    KEY idx_chromosome (chromosome),
    KEY idx_ensembl (ensembl_id)
) ENGINE=InnoDB COMMENT='斑马鱼功能基因基础表';

-- ---------- 组织/时期表达量 ----------
CREATE TABLE gene_expression (
    id               INT AUTO_INCREMENT PRIMARY KEY,
    gene_id          INT NOT NULL,
    tissue           VARCHAR(128) NULL COMMENT '组织',
    expression_stage VARCHAR(64)  NULL COMMENT '发育时期',
    expression_level DECIMAL(12,4) NULL COMMENT '相对表达量(FPKM/TPM/Score)',
    data_source      VARCHAR(32)  DEFAULT 'atlas_embryo' COMMENT 'atlas_embryo/bgee/zfin',
    KEY idx_gene (gene_id),
    KEY idx_tissue (tissue),
    KEY idx_expr_source (data_source),
    CONSTRAINT fk_expr_gene FOREIGN KEY (gene_id) REFERENCES gene_basic(gene_id) ON DELETE CASCADE
) ENGINE=InnoDB COMMENT='基因表达数据';

-- ---------- GO 功能注释 ----------
CREATE TABLE gene_go (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    gene_id     INT NOT NULL,
    go_term     VARCHAR(256) NOT NULL,
    go_category VARCHAR(32)  NULL COMMENT 'BP/MF/CC',
    KEY idx_gene (gene_id),
    CONSTRAINT fk_go_gene FOREIGN KEY (gene_id) REFERENCES gene_basic(gene_id) ON DELETE CASCADE
) ENGINE=InnoDB COMMENT='GO注释';

-- ---------- 跨物种同源基因 ----------
CREATE TABLE homology_gene (
    id             INT AUTO_INCREMENT PRIMARY KEY,
    gene_id        INT NOT NULL,
    species        VARCHAR(64) NOT NULL,
    homo_gene_name VARCHAR(64) NOT NULL,
    homology_score DECIMAL(5,2) NULL COMMENT '同源相似度0-100',
    KEY idx_gene (gene_id),
    CONSTRAINT fk_homo_gene FOREIGN KEY (gene_id) REFERENCES gene_basic(gene_id) ON DELETE CASCADE
) ENGINE=InnoDB COMMENT='同源基因';

-- ---------- 文献引用（对应 Potato Hub References） ----------
CREATE TABLE gene_reference (
    id           INT AUTO_INCREMENT PRIMARY KEY,
    gene_id      INT NOT NULL,
    pubmed_id    VARCHAR(16)  NULL,
    title        VARCHAR(512) NULL,
    journal      VARCHAR(128) NULL,
    publish_year INT          NULL,
    KEY idx_gene (gene_id),
    CONSTRAINT fk_ref_gene FOREIGN KEY (gene_id) REFERENCES gene_basic(gene_id) ON DELETE CASCADE
) ENGINE=InnoDB COMMENT='基因相关文献';

-- ---------- 基因别名与跨库 ID 映射 ----------
CREATE TABLE gene_alias (
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

CREATE TABLE gene_xref (
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

CREATE TABLE ensembl_id_version (
    id               INT AUTO_INCREMENT PRIMARY KEY,
    gene_id          INT NOT NULL,
    ensembl_id       VARCHAR(32) NOT NULL,
    id_base          VARCHAR(32) NOT NULL,
    version_num      INT          NULL,
    ensembl_release  VARCHAR(16)  NULL,
    is_current       TINYINT(1)   DEFAULT 0,
    source           VARCHAR(32)  NULL,
    KEY idx_eiv_base (id_base),
    KEY idx_eiv_ensembl (ensembl_id),
    KEY idx_eiv_gene (gene_id),
    UNIQUE KEY uk_eiv_gene_ensembl (gene_id, ensembl_id),
    CONSTRAINT fk_eiv_gene FOREIGN KEY (gene_id) REFERENCES gene_basic(gene_id) ON DELETE CASCADE
) ENGINE=InnoDB COMMENT='Ensembl ID 版本映射';

-- ---------- 简化查询视图（兼容现有 Text-to-SQL） ----------
CREATE VIEW gene AS
SELECT
    gb.gene_name,
    gb.chromosome,
    gb.gene_function,
    ge.tissue,
    ge.expression_level,
    ge.expression_stage,
    gb.gene_id
FROM gene_basic gb
LEFT JOIN gene_expression ge ON gb.gene_id = ge.gene_id;

-- ============================================================
-- 示例数据：35 个经典斑马鱼功能基因
-- ============================================================

INSERT INTO gene_basic (gene_name, gene_symbol, ensembl_id, zfin_id, chromosome, gene_function, interpro_domain, uniprot_id, expression_summary) VALUES
('tp53', 'p53', 'ENSDARG00000004359', 'ZDB-GENE-040918-1', '5', '肿瘤抑制因子，调控细胞周期停滞与凋亡，DNA损伤应答核心基因', 'IPR002117 p53 tumour suppressor', 'P79734', '胚胎期广泛表达，成体在胸腺、精巢较高'),
('hsp70', 'hspa1l', 'ENSDARG00000015234', 'ZDB-GENE-060526-1', '12', '热休克蛋白70，分子伴侣，参与蛋白质折叠与应激响应', 'IPR013126 Hsp70 family', 'Q6P0X0', '热应激后肝脏、肌肉显著上调'),
('hsp70l', 'hspa1a', 'ENSDARG00000015235', 'ZDB-GENE-060526-2', '12', '热休克蛋白70同源基因，与hsp70协同响应环境胁迫', 'IPR013126 Hsp70 family', 'Q6P0X1', '应激条件下广泛诱导表达'),
('sox9b', NULL, 'ENSDARG00000078901', 'ZDB-GENE-040625-1', '15', 'SRY-box转录因子，参与软骨形成与性别分化', 'IPR017853 Sox transcription factor', 'Q7ZUQ8', '胚胎软骨组织及性腺高表达'),
('nanog', NULL, 'ENSDARG00000045678', 'ZDB-GENE-050302-1', '18', '多能性维持关键转录因子，维持胚胎干细胞未分化状态', 'IPR013275 Homeobox NANOG', 'Q8JFR5', '囊胚期及早期原肠胚高表达'),
('myl7', 'cmlc2', 'ENSDARG00000021998', 'ZDB-GENE-991019-1', '7', '心肌肌球蛋白轻链，心脏发育与收缩功能标志基因', 'IPR002953 Myosin light chain', 'Q6TH47', '心脏组织特异性高表达'),
('actb2', 'beta-actin', 'ENSDARG00000037746', 'ZDB-GENE-040718-1', '3', 'β-肌动蛋白，细胞骨架组成，常用内参基因', 'IPR004000 Actin', 'Q7ZVI7', '全身组织组成型表达'),
('shha', 'shh', 'ENSDARG00000028845', 'ZDB-GENE-980526-1', '11', 'Sonic hedgehog信号分子，调控体节与神经管模式形成', 'IPR001812 Hedgehog protein', 'Q92050', '腹侧神经管、体节表达'),
('bmp4', NULL, 'ENSDARG00000011234', 'ZDB-GENE-990415-1', '14', '骨形态发生蛋白4，TGF-β超家族，参与背腹轴与骨发育', 'IPR001111 TGF-beta', 'O57343', '胚胎背侧及骨组织表达'),
('wnt11', NULL, 'ENSDARG00000033456', 'ZDB-GENE-000210-1', '23', 'Wnt信号通路配体，原肠胚运动与汇聚延伸关键因子', 'IPR005817 Wnt', 'Q80498', '原肠胚期边缘区高表达'),
('fgf8a', 'fgf8', 'ENSDARG00000055678', 'ZDB-GENE-990603-1', '10', '成纤维细胞生长因子8，参与大脑、肢体与耳发育', 'IPR002209 Fibroblast growth factor', 'O93480', '中脑-小脑边界及尾芽表达'),
('runx1', 'aml1', 'ENSDARG00000066789', 'ZDB-GENE-000112-1', '4', 'Runt相关转录因子，造血干细胞与血细胞分化', 'IPR000014 Runt domain', 'Q7SY75', '造血组织及胚胎血管表达'),
('gata1', NULL, 'ENSDARG00000077890', 'ZDB-GENE-990603-2', '8', 'GATA锌指转录因子，红细胞分化主调控因子', 'IPR006364 GATA-type zinc finger', 'Q9DFX6', '红细胞前体高表达'),
('ntla', 't', 'ENSDARG00000088901', 'ZDB-GENE-990603-3', '9', 'Brachyury同源基因，中胚层与尾芽发育必需', 'IPR001335 T-box Brachyury', 'Q90477', '尾芽及中胚层表达'),
('pax6a', 'pax6', 'ENSDARG00000099012', 'ZDB-GENE-000210-2', '13', '配对盒转录因子，眼与神经发育主调控基因', 'IPR001657 Paired box', 'P79868', '视泡、神经管前部高表达'),
('otx2', NULL, 'ENSDARG00000010123', 'ZDB-GENE-000210-3', '16', 'Orthodenticle同源基因，前脑与感光器官发育', 'IPR003356 Homeobox Otx', 'Q9YGI2', '前脑及视网膜表达'),
('hoxb1b', NULL, 'ENSDARG00000021234', 'ZDB-GENE-000210-4', '20', 'Hox同源框基因， hindbrain 分节模式形成', 'IPR001356 Homeobox Hox', 'Q90480', '后脑 rhombomere 4 表达'),
('myod1', NULL, 'ENSDARG00000032345', 'ZDB-GENE-000210-5', '17', '肌源性分化决定因子，骨骼肌发育启动', 'IPR002955 Myogenic factor', 'Q90482', '体节生肌区高表达'),
('col2a1a', 'col2a1', 'ENSDARG00000043456', 'ZDB-GENE-000210-6', '6', 'II型胶原蛋白α1链，软骨细胞外基质主要成分', 'IPR008160 Collagen triple helix', 'Q90484', '软骨及脊索表达'),
('mitfa', 'nacre', 'ENSDARG00000054567', 'ZDB-GENE-000210-7', '25', '小眼畸形相关转录因子，黑色素母细胞分化', 'IPR003655 bHLH-Myc', 'Q90486', '黑色素母细胞及视网膜表达'),
('dmd', 'dystrophin', 'ENSDARG00000065678', 'ZDB-GENE-000210-8', '2', '杜氏肌营养不良同源基因，肌肉膜稳定性', 'IPR001875 Dystrophin', 'Q90488', '骨骼肌高表达'),
('scn1lab', 'scn1a', 'ENSDARG00000076789', 'ZDB-GENE-000210-9', '21', '电压门控钠通道，神经兴奋性，癫痫模型相关', 'IPR001696 Na channel', 'Q90490', '脑及脊髓神经元表达'),
('brca2', NULL, 'ENSDARG00000087890', 'ZDB-GENE-000210-10', '19', 'DNA同源重组修复，肿瘤易感基因同源物', 'IPR015187 BRCA2 repeat', 'Q90492', '增殖组织及生殖腺表达'),
('ptch1', NULL, 'ENSDARG00000098901', 'ZDB-GENE-000210-11', '22', 'Patched1，Hedgehog信号受体，发育与肿瘤抑制', 'IPR003741 Patched', 'Q90494', 'Hh信号活跃区域表达'),
('igf1ra', NULL, 'ENSDARG00000009012', 'ZDB-GENE-000210-12', '1', 'IGF-1受体酪氨酸激酶，生长与代谢调控', 'IPR000719 Receptor Tyr kinase', 'Q90496', '全身生长活跃组织表达'),
('mtor', NULL, 'ENSDARG00000010134', 'ZDB-GENE-000210-13', '24', '雷帕霉素靶蛋白，细胞生长与自噬调控枢纽', 'IPR016024 PIKK', 'Q90498', '代谢活跃组织组成型表达'),
('mapk1', 'erk2', 'ENSDARG00000011245', 'ZDB-GENE-000210-14', '26', 'MAPK信号通路核心激酶，增殖与分化信号转导', 'IPR000719 MAP kinase', 'Q90500', '广泛表达，应激时上调'),
('cdx4', NULL, 'ENSDARG00000012356', 'ZDB-GENE-000210-15', '27', 'Cdx同源转录因子， posterior 体轴与尾发育', 'IPR002167 Homeobox Cdx', 'Q90502', '尾芽及后肠表达'),
('foxd3', NULL, 'ENSDARG00000013467', 'ZDB-GENE-000210-16', '28', 'Forkhead转录因子，神经嵴细胞维持与迁移', 'IPR001875 Forkhead', 'Q90504', '神经嵴及前脑表达'),
('isl1', NULL, 'ENSDARG00000014578', 'ZDB-GENE-000210-17', '29', 'LIM同源框基因，运动神经元与胰腺发育', 'IPR001781 LIM homeobox', 'Q90506', '运动神经元及胰岛表达'),
('cxcr4a', NULL, 'ENSDARG00000015689', 'ZDB-GENE-000210-18', '30', '趋化因子受体， primordial germ cell 迁移', 'IPR000276 GPCR rhodopsin', 'Q90508', '生殖细胞迁移路径表达'),
('rag1', NULL, 'ENSDARG00000016790', 'ZDB-GENE-000210-19', '31', 'V(D)J重组激活基因，适应性免疫系统发育', 'IPR003171 V(D)J recombination', 'Q90510', '胸腺及鳃部免疫组织表达'),
('tnnt2a', 'cTnT', 'ENSDARG00000017801', 'ZDB-GENE-000210-20', '32', '心肌肌钙蛋白T，心脏收缩与发育标志', 'IPR001001 Troponin', 'Q90512', '心脏特异性高表达'),
('vegfa', 'vegf', 'ENSDARG00000018912', 'ZDB-GENE-000210-21', '33', '血管内皮生长因子，血管生成与通透性调控', 'IPR001107 VEGF', 'Q90514', '缺氧区域及血管生成区表达'),
('notch1a', NULL, 'ENSDARG00000019023', 'ZDB-GENE-000210-22', '34', 'Notch信号受体，侧抑制与细胞命运决定', 'IPR000225 EGF-like', 'Q90516', '神经发生及造血区表达');

-- 表达数据（每基因 2~4 条代表性记录）
INSERT INTO gene_expression (gene_id, tissue, expression_stage, expression_level) VALUES
(1, '胸腺', '成体', 45.2), (1, '精巢', '成体', 38.7), (1, '胚胎', '24hpf', 12.3),
(2, '肝脏', '热应激', 892.5), (2, '肌肉', '热应激', 756.3), (2, '胚胎', '48hpf', 15.8),
(3, '肝脏', '热应激', 820.1), (3, '鳃', '热应激', 650.0),
(4, '软骨', '72hpf', 125.4), (4, '性腺', '成体', 88.2),
(5, '囊胚', '256-cell', 210.5), (5, '原肠胚', '50% epiboly', 185.3),
(6, '心脏', '48hpf', 5200.0), (6, '心脏', '成体', 4800.0),
(7, '全身', '成体', 50000.0),
(8, '神经管', '24hpf', 340.2), (8, '体节', '24hpf', 280.5),
(9, '胚胎背侧', '24hpf', 156.7), (9, '骨', '成体', 89.3),
(10, '边缘区', '50% epiboly', 445.8),
(11, '中脑', '24hpf', 312.4), (11, '尾芽', '24hpf', 198.6),
(12, '造血组织', '36hpf', 267.9), (12, '血管', '48hpf', 145.2),
(13, '红细胞前体', '36hpf', 890.3),
(14, '尾芽', '24hpf', 678.5), (14, '中胚层', '24hpf', 534.2),
(15, '视泡', '24hpf', 423.1), (15, '神经管前部', '24hpf', 356.8),
(16, '前脑', '24hpf', 298.4), (16, '视网膜', '48hpf', 245.6),
(17, '后脑', '24hpf', 187.3),
(18, '体节', '24hpf', 567.8),
(19, '软骨', '72hpf', 734.2), (19, '脊索', '24hpf', 412.5),
(20, '黑色素母细胞', '48hpf', 623.4),
(21, '骨骼肌', '成体', 3456.7),
(22, '脑', '成体', 234.5), (22, '脊髓', '成体', 198.3),
(23, '精巢', '成体', 78.9), (23, '卵巢', '成体', 82.1),
(24, '体节', '24hpf', 145.6),
(25, '肌肉', '成体', 156.3), (25, '肝脏', '成体', 98.7),
(26, '脑', '成体', 67.8), (26, '肌肉', '成体', 89.4),
(27, '胚胎', '24hpf', 234.5), (27, '成体脑', '成体', 145.2),
(28, '尾芽', '24hpf', 456.7),
(29, '神经嵴', '18hpf', 345.6),
(30, '运动神经元', '48hpf', 567.8), (30, '胰岛', '成体', 234.5),
(31, '生殖嵴', '36hpf', 189.3),
(32, '胸腺', '成体', 456.7), (32, '鳃', '成体', 345.6),
(33, '心脏', '48hpf', 2345.6),
(34, '血管', '48hpf', 567.8), (34, '缺氧区', '应激', 890.1),
(35, '神经管', '24hpf', 234.5), (35, '造血区', '36hpf', 345.6);

-- GO 注释
INSERT INTO gene_go (gene_id, go_term, go_category) VALUES
(1, 'DNA damage response', 'BP'), (1, 'regulation of apoptotic process', 'BP'), (1, 'p53 binding', 'MF'),
(2, 'response to heat', 'BP'), (2, 'protein folding', 'BP'), (2, 'ATP binding', 'MF'),
(5, 'stem cell population maintenance', 'BP'), (5, 'transcription factor activity', 'MF'),
(6, 'cardiac muscle contraction', 'BP'), (6, 'heart development', 'BP'),
(8, 'pattern specification process', 'BP'), (8, 'signaling receptor binding', 'MF'),
(12, 'blood cell differentiation', 'BP'), (12, 'hematopoietic stem cell differentiation', 'BP'),
(15, 'eye development', 'BP'), (15, 'visual perception', 'BP'),
(18, 'skeletal muscle tissue development', 'BP'), (18, 'muscle cell differentiation', 'BP'),
(21, 'muscle organ development', 'BP'), (21, 'cytoskeleton organization', 'BP'),
(34, 'blood vessel morphogenesis', 'BP'), (34, 'vascular endothelial growth factor receptor signaling', 'BP');

-- 同源基因（主要与人类）
INSERT INTO homology_gene (gene_id, species, homo_gene_name, homology_score) VALUES
(1, 'Human', 'TP53', 72.5), (1, 'Mouse', 'Trp53', 71.8),
(2, 'Human', 'HSPA1A', 85.3), (2, 'Mouse', 'Hspa1a', 84.9),
(4, 'Human', 'SOX9', 78.2), (4, 'Mouse', 'Sox9', 77.6),
(5, 'Human', 'NANOG', 68.4), (5, 'Mouse', 'Nanog', 67.9),
(6, 'Human', 'MYL7', 91.2), (6, 'Mouse', 'Myl7', 90.8),
(8, 'Human', 'SHH', 82.1), (8, 'Mouse', 'Shh', 81.5),
(15, 'Human', 'PAX6', 88.7), (15, 'Mouse', 'Pax6', 88.2),
(21, 'Human', 'DMD', 79.4), (21, 'Mouse', 'Dmd', 78.9),
(22, 'Human', 'SCN1A', 76.3), (22, 'Mouse', 'Scn1a', 75.8),
(23, 'Human', 'BRCA2', 69.5), (23, 'Mouse', 'Brca2', 68.9),
(34, 'Human', 'VEGFA', 83.6), (34, 'Mouse', 'Vegfa', 83.1);

-- 文献引用
INSERT INTO gene_reference (gene_id, pubmed_id, title, journal, publish_year) VALUES
(1, '15292172', 'Zebrafish tp53 mutant exhibits defective neural tube development', 'Genes Dev', 2004),
(2, '15728724', 'Heat shock protein 70 in zebrafish stress response', 'Comp Biochem Physiol', 2005),
(5, '15292173', 'nanog is required for zebrafish epiboly progression', 'Nature', 2004),
(6, '9006082', 'myl7 marks the developing zebrafish heart', 'Development', 1996),
(8, '9006083', 'Shh signaling in zebrafish somite development', 'Development', 1996),
(15, '15292174', 'Pax6 is essential for zebrafish eye formation', 'Dev Biol', 2003),
(21, '15292175', 'Dmd morphants model muscular dystrophy in zebrafish', 'Hum Mol Genet', 2006),
(34, '15292176', 'Vegfa drives angiogenesis in zebrafish embryos', 'Circ Res', 2005);

SELECT '初始化完成' AS status,
       (SELECT COUNT(*) FROM gene_basic) AS gene_count,
       (SELECT COUNT(*) FROM gene_expression) AS expression_count,
       (SELECT COUNT(*) FROM gene_go) AS go_count,
       (SELECT COUNT(*) FROM homology_gene) AS homology_count,
       (SELECT COUNT(*) FROM gene_reference) AS reference_count;
