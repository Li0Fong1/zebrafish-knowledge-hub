-- PubMed 原始文献表（NCBI efetch XML，入库前不做任何处理）
USE zebrafish_db;

CREATE TABLE IF NOT EXISTS literature_pubmed (
    pmid           VARCHAR(16)  NOT NULL PRIMARY KEY COMMENT 'PubMed ID',
    raw_xml        MEDIUMTEXT   NOT NULL COMMENT 'NCBI efetch 原始 PubmedArticle XML',
    search_query   VARCHAR(512) NULL COMMENT '采集检索式',
    fetched_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY idx_fetched (fetched_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='PubMed 原始文献（未加工）';

CREATE TABLE IF NOT EXISTS literature_import_job (
    id             INT AUTO_INCREMENT PRIMARY KEY,
    job_name       VARCHAR(64)  NOT NULL,
    search_query   VARCHAR(512) NOT NULL,
    total_expected INT          NULL,
    imported_count INT          DEFAULT 0,
    retstart       INT          DEFAULT 0,
    status         VARCHAR(32)  DEFAULT 'running' COMMENT 'running|done|failed',
    last_error     TEXT         NULL,
    started_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at    DATETIME     NULL,
    KEY idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='文献导入任务进度';
