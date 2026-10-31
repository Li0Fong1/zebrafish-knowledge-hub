-- PubMed 斑马鱼研究对象判别结果（全库）
USE zebrafish_db;

CREATE TABLE IF NOT EXISTS literature_pubmed_judgement (
    pmid        VARCHAR(16)  NOT NULL PRIMARY KEY COMMENT 'PubMed ID',
    title_en    TEXT         NULL,
    abstract_en MEDIUMTEXT   NULL,
    title_zh    TEXT         NULL,
    abstract_zh MEDIUMTEXT   NULL,
    judgement   VARCHAR(16)  NOT NULL COMMENT '是|否|无法判别',
    reason      VARCHAR(512) NULL,
    mode        VARCHAR(16)  DEFAULT 'llm' COMMENT 'llm|rule',
    judged_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY idx_judgement (judgement),
    KEY idx_judged_at (judged_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='PubMed 斑马鱼研究对象判别';
