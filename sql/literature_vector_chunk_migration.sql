-- PubMed「是」文献向量 chunk 元数据（向量本体在 FAISS 分片文件）
USE zebrafish_db;

CREATE TABLE IF NOT EXISTS literature_vector_chunk (
    chunk_id     BIGINT AUTO_INCREMENT PRIMARY KEY,
    pmid         VARCHAR(16)  NOT NULL,
    shard_id     SMALLINT     NOT NULL DEFAULT 0,
    chunk_index  SMALLINT     NOT NULL DEFAULT 0,
    has_fulltext TINYINT(1)   NOT NULL DEFAULT 0,
    title_zh     VARCHAR(512) NULL,
    text_preview VARCHAR(300) NULL,
    embedded_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY idx_pmid (pmid),
    KEY idx_shard (shard_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='PubMed YES 文献向量 chunk 元数据';
