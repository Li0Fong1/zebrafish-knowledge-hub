# 立项：只读 MySQL 账号（纵深防御）

> 状态：代码侧已就绪，**待 DBA 建号**后切流。
> 关联审计项：安全 #10「MySQL 写权限面过宽」。

## 1. 为什么要做

当前应用所有查询与写入共用同一个账号（`zebrafish`），该账号对业务库有
`INSERT/UPDATE/DELETE/CREATE/ALTER` 权限。虽然：

- `execute_sql()` 静态只放行 `SELECT`；
- `execute_write()` 默认关闭（需 `MYSQL_ALLOW_WRITE=1`）；
- `build_fallback_sql()` 用 `_safe_gene_token()` 白名单过滤标识符；

但这三道都是**应用层**的闸门。一旦出现拼接疏漏、依赖升级导致的执行路径变化、
或有人误用 `execute_write`，损失是**真实数据**。数据库层的只读账号是最后一道、
也是唯一不依赖应用代码正确性的防线。

## 2. 目标与非目标

**目标**
- 所有 `SELECT` 走只读账号；写路径（账号、会话历史、运维脚本）仍走主账号。
- 只读账号连不上时**自动回退主账号**，不因建号未完成而影响可用性。
- 切换零停机、可一键回滚（删掉环境变量即可）。

**非目标**
- 不改任何 SQL 语义、不改表结构。
- 不做「按接口分账号」（当前调用面还不需要）。

## 3. 设计

`core/config.py` 新增可选配置：

```python
DB_READONLY_CONFIG = None          # 未配置时为 None，行为与现在完全一致
# MYSQL_RO_USER 非空时才会构建，host/port/database 默认继承主账号
```

`database/mysql_client.py`：

- 主引擎 `engine` / `SessionLocal`（可写，`get_db()` 用它）；
- 只读引擎 `ro_engine` / `RoSessionLocal`，**启动时探测**，失败只告警并置空；
- `execute_sql()` → `_session(readonly=True)`（有只读引擎就用，没有就回退）；
- `execute_write()` → 恒走主引擎，且仍受 `MYSQL_ALLOW_WRITE` 管控；
- 断连自愈 `_run()` 会同时 dispose 两个引擎后重建。

这样**不需要改任何调用点**：所有现存 `get_mysql_client().execute_sql(...)`
自动切到只读连接。

## 4. 落地步骤

| 步骤 | 负责 | 内容 |
|---|---|---|
| 1 | DBA | 执行 `scripts/sql/01_create_readonly_user.sql`，替换 `<STRONG_PASSWORD>` |
| 2 | DBA | `SHOW GRANTS` 验收：仅 `USAGE` + `SELECT` |
| 3 | 运维 | 在服务器 `.env` 追加 `MYSQL_RO_USER` / `MYSQL_RO_PASSWORD`（**不要提交仓库**） |
| 4 | 研发 | 推送 `core/config.py`、`database/mysql_client.py`，重启 Hub |
| 5 | 研发 | 看日志出现 `[成功] MySQL只读连接成功（zebrafish_ro@127.0.0.1:3307）` |
| 6 | 研发 | 抽检：`/api/chat` 的统计题、表达谱、基因详情均正常 |

`.env` 追加示例（口令由 DBA 私下传递）：

```
MYSQL_RO_USER=zebrafish_ro
MYSQL_RO_PASSWORD=<DBA 生成的口令>
# 同机部署时下面三项可省略
# MYSQL_RO_HOST=127.0.0.1
# MYSQL_RO_PORT=3307
# MYSQL_RO_DATABASE=zebrafish_db
```

## 5. 验收标准

1. 启动日志出现只读连接成功；无 `回退主账号` 告警。
2. `SELECT COUNT(*) FROM gene_basic` 经应用返回 32,139。
3. 用只读账号手工执行 `DELETE` / `CREATE` 均被数据库拒绝（1142/1044）。
4. 主账号口令输错时，只读查询**仍然可用**（证明读路径确实独立）。
5. 删除 `MYSQL_RO_USER` 后重启，功能与现状一致（回滚验证）。

## 6. 风险与回滚

| 风险 | 缓解 |
|---|---|
| 只读账号缺少某张新表的 SELECT | 连接探测只验证 `SELECT 1`；运行期报错会 `logger.error`。上线首周观察 `SQL执行失败` 日志 |
| 只读库指向了只读实例（延迟） | 本方案默认同实例同端口，不引入复制延迟；如未来接只读副本需评估一致性 |
| 口令轮换导致连接失败 | 失败自动回退主账号，不阻断服务；改 `.env` 后重启即可 |

**回滚**：删除 `.env` 中 `MYSQL_RO_*` 三行并重启。代码无需回退。

## 7. 工作量与排期

- 代码：已完成（`core/config.py`、`database/mysql_client.py`）。
- DBA：10 分钟（建号 + 授权 + 验收）。
- 运维/研发：10 分钟（改 `.env` + 重启 + 抽检）。
