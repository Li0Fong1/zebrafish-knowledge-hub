-- 可追加文献库骨架（事项三）：papers / fulltext_gold / vector_shards / jobs / sync_log
-- 语义对齐策略；表名加 literature_ 前缀，避免与通用名冲突。
USE zebrafish_db;

-- 文献主档（PMID / ENO 幂等）
CREATE TABLE IF NOT EXISTS literature_paper (
    doc_id          VARCHAR(64)  NOT NULL PRIMARY KEY COMMENT 'pmid:123 / eno:26672',
    pmid            VARCHAR(16)  NULL COMMENT 'PubMed ID',
    entry_no        VARCHAR(32)  NULL COMMENT 'pdf库 ENO',
    doi             VARCHAR(128) NULL,
    pmcid           VARCHAR(32)  NULL,
    source          VARCHAR(32)  NOT NULL DEFAULT 'pubmed_yes' COMMENT 'pubmed_yes|exist_eno|wos|...',
    title_en        TEXT         NULL,
    title_zh        TEXT         NULL,
    year            SMALLINT     NULL,
    journal         VARCHAR(512) NULL,
    judgement       VARCHAR(16)  NULL COMMENT '是|否|无法判别',
    has_fulltext    TINYINT(1)   NOT NULL DEFAULT 0,
    gold_status     VARCHAR(32)  NULL COMMENT 'none|partial|ready|...',
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_pmid (pmid),
    UNIQUE KEY uk_entry_no (entry_no),
    KEY idx_source (source),
    KEY idx_judgement (judgement),
    KEY idx_gold (gold_status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='可追加文献主档';

-- 金标全文（版本化；正文可外置路径）
CREATE TABLE IF NOT EXISTS literature_fulltext_gold (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    doc_id          VARCHAR(64)  NOT NULL,
    gold_version    VARCHAR(32)  NOT NULL DEFAULT 'v1',
    source_tier     VARCHAR(64)  NULL COMMENT 'pdf_align|xml_jats_fulltext|...',
    body_path       VARCHAR(1024) NULL,
    body_text       MEDIUMTEXT   NULL,
    sections_json   MEDIUMTEXT   NULL,
    content_sha256  CHAR(64)     NULL,
    campaign_id     VARCHAR(64)  NULL,
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_doc_ver (doc_id, gold_version),
    KEY idx_tier (source_tier),
    KEY idx_campaign (campaign_id),
    CONSTRAINT fk_gold_paper FOREIGN KEY (doc_id) REFERENCES literature_paper (doc_id)
        ON UPDATE CASCADE ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='金标全文版本表';

-- FAISS 分片/索引版本登记（向量本体仍在磁盘）
CREATE TABLE IF NOT EXISTS literature_vector_shard (
    id                  BIGINT AUTO_INCREMENT PRIMARY KEY,
    shard_id            INT          NOT NULL COMMENT '目录内分片序号',
    index_version       VARCHAR(64)  NOT NULL COMMENT '如 bge_small_v1 / gte_mbase_v1',
    root_path           VARCHAR(1024) NOT NULL,
    shard_path          VARCHAR(1024) NULL,
    embedding_model     VARCHAR(256) NOT NULL,
    embedding_dim       INT          NOT NULL,
    paper_count         INT          NOT NULL DEFAULT 0,
    chunk_count         INT          NOT NULL DEFAULT 0,
    status              VARCHAR(16)  NOT NULL DEFAULT 'building' COMMENT 'building|ready|shadow|retired|readonly',
    kind                VARCHAR(64)  NULL COMMENT 'pmid_fulltext|exist_eno|trial|...',
    manifest_json       MEDIUMTEXT   NULL COMMENT '摘要或单分片 info',
    created_at          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_ver_shard (index_version, shard_id),
    KEY idx_status (status),
    KEY idx_model (embedding_model)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='FAISS 分片版本登记';

-- 流水线任务（对齐 import_job 模式，覆盖 embed/gold/migrate/ab）
CREATE TABLE IF NOT EXISTS literature_pipeline_job (
    job_id          BIGINT AUTO_INCREMENT PRIMARY KEY,
    job_type        VARCHAR(32)  NOT NULL COMMENT 'embed|gold_hunt|migrate|ab_weights|append_paper|register_shard',
    status          VARCHAR(32)  NOT NULL DEFAULT 'running' COMMENT 'running|done|failed|cancelled',
    progress        VARCHAR(128) NULL,
    last_error      TEXT         NULL,
    params_json     MEDIUMTEXT   NULL,
    index_version   VARCHAR(64)  NULL,
    started_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at     DATETIME     NULL,
    KEY idx_type_status (job_type, status),
    KEY idx_index_ver (index_version)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='文献流水线任务';

-- 可追溯同步日志
CREATE TABLE IF NOT EXISTS literature_sync_log (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    event_time      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    actor           VARCHAR(64)  NOT NULL DEFAULT 'system',
    action          VARCHAR(64)  NOT NULL COMMENT 'append_paper|register_shard|hub_switch|dryrun|...',
    doc_id          VARCHAR(64)  NULL,
    index_version   VARCHAR(64)  NULL,
    detail_json     MEDIUMTEXT   NULL,
    KEY idx_event (event_time),
    KEY idx_action (action),
    KEY idx_doc (doc_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='文献库同步事件日志';
