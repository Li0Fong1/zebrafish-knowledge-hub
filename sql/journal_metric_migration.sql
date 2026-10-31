-- 期刊影响因子 / 分层表（本地快照，检索时不打在线 API）
USE zebrafish_db;

CREATE TABLE IF NOT EXISTS journal_metric (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    journal_key VARCHAR(256) NOT NULL COMMENT 'norm_journal 后键',
    display_name VARCHAR(512) NULL,
    issn_print VARCHAR(16) NULL,
    issn_electronic VARCHAR(16) NULL,
    if_value DECIMAL(8,3) NULL COMMENT 'JCR 或代理值',
    if_year SMALLINT NULL COMMENT 'IF 发布年',
    if_source VARCHAR(32) NOT NULL DEFAULT 'curated' COMMENT 'jcr|openalex|curated|manual',
    tier CHAR(2) NOT NULL DEFAULT 'T4' COMMENT 'T0..T4',
    tier_boost DECIMAL(4,3) NOT NULL DEFAULT 0.450,
    if_norm DECIMAL(4,3) NOT NULL DEFAULT 0.450,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_journal_key (journal_key),
    KEY idx_issn_p (issn_print),
    KEY idx_issn_e (issn_electronic)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
