-- 多租户 + 工作流（增量迁移）
USE zebrafish_db;

CREATE TABLE IF NOT EXISTS tenant (
    tenant_id     INT AUTO_INCREMENT PRIMARY KEY,
    slug          VARCHAR(64)  NOT NULL UNIQUE COMMENT 'URL/Header 标识',
    name          VARCHAR(128) NOT NULL,
    plan          VARCHAR(32)  DEFAULT 'standard',
    is_active     TINYINT(1)   DEFAULT 1,
    created_at    TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP    DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB COMMENT='租户';

CREATE TABLE IF NOT EXISTS workflow_definition (
    workflow_id   INT AUTO_INCREMENT PRIMARY KEY,
    tenant_id     INT          NULL COMMENT 'NULL=系统预置模板',
    slug          VARCHAR(64)  NOT NULL,
    name          VARCHAR(128) NOT NULL,
    description   TEXT         NULL,
    definition_json JSON       NOT NULL,
    is_preset     TINYINT(1)   DEFAULT 0,
    is_active     TINYINT(1)   DEFAULT 1,
    created_at    TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_tenant_slug (tenant_id, slug),
    KEY idx_preset (is_preset, is_active)
) ENGINE=InnoDB COMMENT='工作流定义';

CREATE TABLE IF NOT EXISTS workflow_run (
    run_id        BIGINT AUTO_INCREMENT PRIMARY KEY,
    tenant_id     INT          NOT NULL,
    workflow_id   INT          NULL,
    preset_slug   VARCHAR(64)  NULL,
    input_text    TEXT         NULL,
    status        VARCHAR(16)  DEFAULT 'pending' COMMENT 'pending/running/success/failed',
    output_json   JSON         NULL,
    error_message TEXT         NULL,
    started_at    TIMESTAMP    NULL,
    finished_at   TIMESTAMP    NULL,
    created_at    TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
    KEY idx_tenant_status (tenant_id, status, created_at)
) ENGINE=InnoDB COMMENT='工作流运行记录';

CREATE TABLE IF NOT EXISTS workflow_run_step (
    step_id       BIGINT AUTO_INCREMENT PRIMARY KEY,
    run_id        BIGINT       NOT NULL,
    node_id       VARCHAR(64)  NOT NULL,
    node_type     VARCHAR(32)  NOT NULL,
    label         VARCHAR(128) NULL,
    status        VARCHAR(16)  DEFAULT 'pending',
    summary       TEXT         NULL,
    output_json   JSON         NULL,
    elapsed_ms    INT          DEFAULT 0,
    sort_order    INT          DEFAULT 0,
    KEY idx_run (run_id, sort_order)
) ENGINE=InnoDB COMMENT='工作流步骤';
