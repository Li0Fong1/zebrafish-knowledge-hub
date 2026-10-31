-- 文献主档稳定自编号 ZFQA-000001 …（按 doc_id 排序回填）
USE zebrafish_db;

ALTER TABLE literature_paper
    ADD COLUMN zfqa_id VARCHAR(16) NULL COMMENT 'ZFQA stable id' AFTER doc_id;

ALTER TABLE literature_paper
    ADD UNIQUE KEY uk_zfqa_id (zfqa_id);
