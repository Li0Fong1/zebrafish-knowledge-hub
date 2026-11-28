-- ============================================================================
-- 只读数据库账号（DBA 执行）
-- 目的：把「查询」与「写入」拆成两个账号，做到纵深防御。
--       应用侧只读账号只能 SELECT；即使某处 SQL 拼接失控，也不可能改坏数据。
-- 执行前请确认：
--   1) 下面 DB 名与线上库一致（先跑 SELECT DATABASE(); 或在业务库上执行本脚本）；
--   2) 口令由 DBA 生成（≥20 位随机串），不要复用主账号口令，也不要写进仓库；
--   3) 主机位按实际来源收紧（应用与库同机时用 127.0.0.1，跨机则填应用内网 IP）。
-- ============================================================================

-- 占位：把 <STRONG_PASSWORD> 换成 DBA 生成的口令
SET @ro_user = 'zebrafish_ro';
SET @ro_host = '127.0.0.1';
SET @ro_pass = '<STRONG_PASSWORD>';

-- 1) 建号（已存在则改口令）
CREATE USER IF NOT EXISTS 'zebrafish_ro'@'127.0.0.1' IDENTIFIED BY '<STRONG_PASSWORD>';
ALTER USER 'zebrafish_ro'@'127.0.0.1' IDENTIFIED BY '<STRONG_PASSWORD>';

-- 2) 只给 SELECT（库名按实际调整）
GRANT SELECT ON zebrafish_db.* TO 'zebrafish_ro'@'127.0.0.1';

-- 3) 兜底收回写/管理类权限（防止历史上存在全局授权）
REVOKE INSERT, UPDATE, DELETE, CREATE, DROP, ALTER, INDEX, REFERENCES,
       LOCK TABLES, CREATE TEMPORARY TABLES, EXECUTE, EVENT, TRIGGER,
       CREATE VIEW, SHOW VIEW, CREATE ROUTINE, ALTER ROUTINE, FILE,
       GRANT OPTION, RELOAD, SHUTDOWN, PROCESS, SUPER
  ON zebrafish_db.* FROM 'zebrafish_ro'@'127.0.0.1';

FLUSH PRIVILEGES;

-- 4) 验收：应只看到 USAGE + SELECT
SHOW GRANTS FOR 'zebrafish_ro'@'127.0.0.1';

-- 5) 自检：以下两条应「一成一败」
--    SELECT COUNT(*) FROM gene_basic;              -- 期望：成功
--    DELETE FROM gene_basic WHERE 1=0;             -- 期望：1142 command denied
