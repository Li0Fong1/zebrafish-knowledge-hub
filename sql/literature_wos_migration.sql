-- WoS RIS 原始文献表（单条 RIS 记录原文，入库前不做任何加工）
USE zebrafish_db;

CREATE TABLE IF NOT EXISTS literature_wos (
    wos_ut         VARCHAR(64)  NOT NULL PRIMARY KEY COMMENT 'WoS UT / AN 标识',
    raw_ris        MEDIUMTEXT   NOT NULL COMMENT '单条 RIS 记录原文 (TY..ER)',
    source_file    VARCHAR(512) NULL COMMENT '来源 RIS 文件名',
    pmid           VARCHAR(16)  NULL,
    doi            VARCHAR(256) NULL,
    imported_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY idx_pmid (pmid),
    KEY idx_doi (doi(64)),
    KEY idx_source (source_file(128)),
    KEY idx_imported (imported_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='WoS RIS 原始文献（未加工）';
