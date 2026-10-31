-- Hub 可选账号（快速入门可不注册，全功能可用）
USE zebrafish_db;

CREATE TABLE IF NOT EXISTS hub_user (
    user_id      BIGINT AUTO_INCREMENT PRIMARY KEY,
    username     VARCHAR(64)  NOT NULL,
    password_hash VARCHAR(128) NOT NULL,
    salt         VARCHAR(64)  NOT NULL,
    display_name VARCHAR(64)  NULL,
    created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_login_at DATETIME NULL,
    UNIQUE KEY uk_username (username)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Hub 注册用户';

CREATE TABLE IF NOT EXISTS hub_user_session (
    session_token CHAR(64) NOT NULL PRIMARY KEY,
    user_id      BIGINT NOT NULL,
    created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at   DATETIME NOT NULL,
    KEY idx_user (user_id),
    KEY idx_exp (expires_at),
    CONSTRAINT fk_hub_session_user FOREIGN KEY (user_id) REFERENCES hub_user(user_id)
        ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Hub 登录会话';
