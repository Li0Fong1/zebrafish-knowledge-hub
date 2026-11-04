-- 2026-11-04：给 gene_basic 加「名称类型」标记
--
-- 背景：32,139 条记录里只有 21,088 条有正式基因符号，其余是克隆号、LOC 占位名
-- 和以编号代名的记录。此前每次统计「有多少基因」都要现写一串 NOT LIKE 条件，
-- 容易漏（LOC 那 92 条就漏过）。改为一次性判定、落成列，统计口径就固定了。
--
-- 口径约定：
--   symbol      正式基因符号，对外报「基因数」用这一档
--   clone       克隆 / EST 号（si: wu: zgc: im:）
--   loc         LOC+数字，RefSeq 占位名
--   ensembl_id  以 ENSDARG… 编号代名
--   zfin_id     以 ZDB-GENE-… 编号代名
--
-- 执行后校验：SELECT name_kind, COUNT(*) FROM gene_basic GROUP BY name_kind;
--   预期 symbol 21088 / clone 7493 / ensembl_id 1839 / zfin_id 1627 / loc 92

ALTER TABLE gene_basic
  ADD COLUMN name_kind VARCHAR(16) NOT NULL DEFAULT 'symbol'
  COMMENT '名称类型：symbol 正式符号 / clone 克隆EST号 / loc LOC占位 / ensembl_id 编号代名 / zfin_id 编号代名';

UPDATE gene_basic SET name_kind = 'clone'
 WHERE gene_name LIKE 'si:%' OR gene_name LIKE 'wu:%'
    OR gene_name LIKE 'zgc:%' OR gene_name LIKE 'im:%';

UPDATE gene_basic SET name_kind = 'loc'
 WHERE gene_name LIKE 'LOC%' AND name_kind = 'symbol';

UPDATE gene_basic SET name_kind = 'ensembl_id'
 WHERE gene_name LIKE 'ENSDARG%' AND name_kind = 'symbol';

UPDATE gene_basic SET name_kind = 'zfin_id'
 WHERE gene_name LIKE 'ZDB-%' AND name_kind = 'symbol';

ALTER TABLE gene_basic ADD INDEX idx_name_kind (name_kind);
