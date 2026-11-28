-- 2026-11-13 生产索引补齐（zebrafish_db @ 127.0.0.1:3307）
-- 依据：scripts/_probe_explain.py 实测
--   A/B 基因名解析：SELECT ... FROM gene_basic WHERE gene_name=? OR gene_symbol=? [OR ensembl_id=? OR zfin_id=?]
--       原计划 type=ALL，全表扫 31,979 行
--   C   xref 反查：SELECT DISTINCT gene_id FROM gene_xref WHERE db_id=?
--       原计划扫 uk_gene_db 全部 101,500 条（db_id 为复合索引第二列）
-- 效果（实测 EXPLAIN）：
--   A: ALL 31979 → index_merge(uk_gene_name,idx_gene_symbol) rows=2
--   B: ALL 31979 → index_merge(uk_gene_name,idx_gene_symbol,idx_ensembl,idx_zfin_id) rows=4
--   C: index 101500 → ref(idx_xref_db_id) rows=1
--
-- 幂等：先查 information_schema 再决定是否执行；MySQL 8 在线 DDL 不阻塞读写。

-- 1) gene_basic：符号解析（每道题的基因识别必经路径）
SET @has := (SELECT COUNT(*) FROM information_schema.STATISTICS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'gene_basic'
               AND INDEX_NAME = 'idx_gene_symbol');
SET @sql := IF(@has = 0,
  'ALTER TABLE `gene_basic` ADD INDEX `idx_gene_symbol` (`gene_symbol`), ALGORITHM=INPLACE, LOCK=NONE',
  'DO 0');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

-- 2) gene_basic：ZFIN ID 解析（4 路 OR 中的 zfin_id 分支）
SET @has := (SELECT COUNT(*) FROM information_schema.STATISTICS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'gene_basic'
               AND INDEX_NAME = 'idx_zfin_id');
SET @sql := IF(@has = 0,
  'ALTER TABLE `gene_basic` ADD INDEX `idx_zfin_id` (`zfin_id`), ALGORITHM=INPLACE, LOCK=NONE',
  'DO 0');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

-- 3) gene_xref：按外部库 ID 反查基因
SET @has := (SELECT COUNT(*) FROM information_schema.STATISTICS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'gene_xref'
               AND INDEX_NAME = 'idx_xref_db_id');
SET @sql := IF(@has = 0,
  'ALTER TABLE `gene_xref` ADD INDEX `idx_xref_db_id` (`db_id`), ALGORITHM=INPLACE, LOCK=NONE',
  'DO 0');
PREPARE s FROM @sql; EXECUTE s; DEALLOCATE PREPARE s;

-- 刷新统计，让优化器立即选用新索引
ANALYZE TABLE `gene_basic`;
ANALYZE TABLE `gene_xref`;
