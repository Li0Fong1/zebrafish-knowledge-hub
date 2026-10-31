-- 数据资产登记：磁盘路径进库，不把 PDF/FAISS 二进制塞进 MySQL
USE zebrafish_db;

CREATE TABLE IF NOT EXISTS data_asset_registry (
    asset_id        VARCHAR(64)  NOT NULL PRIMARY KEY COMMENT 'stable key e.g. local:pdf_lib',
    role            VARCHAR(32)  NOT NULL COMMENT 'code|pdf|gold_json|faiss|cache|corpus|model|external',
    stage           VARCHAR(8)   NOT NULL DEFAULT '5' COMMENT '0-5 项目阶段编号',
    location_kind   VARCHAR(16)  NOT NULL COMMENT 'workspace|local_cache|server|optical|other',
    root_path       VARCHAR(1024) NOT NULL,
    file_count      INT          NULL,
    byte_size       BIGINT       NULL,
    notes           VARCHAR(512) NULL,
    in_mysql        TINYINT(1)   NOT NULL DEFAULT 0 COMMENT '1=结构化已在业务表',
    mysql_tables    VARCHAR(512) NULL,
    updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    KEY idx_role (role),
    KEY idx_stage (stage)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='散落数据根目录登记';
