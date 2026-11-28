# API 密码锁与密钥防护说明

更新日期：2026-10-31（2026-11-25 修订：默认关闭）　适用版本：本仓库当前代码

> **2026-11-25 变更（必读）**
>
> 密码锁已改为**默认关闭**（opt-in）。原因：其他机器的访客首次打开页面时拿不到
> 通行证 cookie（首页走 CDN 缓存，`Set-Cookie` 被边缘剥离），而工具栏里的工具
> （CRISPR / 基因检索 / 表型 / 序列等）用的是裸 `fetch`，不会走 `apiFetch` 的
> 自动补票重试，于是这些机器上所有工具一律 401「此接口需要 API 密钥」。
>
> 现在只有**同时**满足 `API_GATE_ENABLED=1` 且配置了 `API_GATE_KEY` 才会启用；
> 不写或写 `0` 时，页面与 `/api/*` 全部免密钥。生产与本地 `.env` 均已设为 `0`。
>
> 仍生效的成本防线：限流（60 次/分钟）、并发闸、失败封禁、密钥不出服务端。

---

## 一、这次改了什么

| # | 项目 | 改动前 | 改动后 |
|---|---|---|---|
| 1 | 页面访问 | `WEB_ACCESS_TOKEN` 有一个 6 位旧令牌 | 令牌清空，`WEB_AUTH_REQUIRED=0`，**打开即用，不弹任何框** |
| 2 | 数据库密码 | `<set-via-env>` | 已迁移为新密码（见 `.env` 的 `MYSQL_PASSWORD`），`root@%` 与 `root@localhost` 两个账户同步更新 |
| 3 | API 访问 | 任何人 `curl` 就能调 `/api/*` | **浏览器自动通行，直连必须带 `X-API-Key`** |
| 4 | 外部 API 密钥 | 明文在 `.env`，无脱敏、无传输校验 | 日志全链路打码、上游报错打码、强制 HTTPS、拒绝内网地址、文件权限收紧 |

---

## 二、三条访问通道

### 通道 A：浏览器（普通用户，零感知）

```
打开 http://127.0.0.1:8080/  →  服务端下发 HttpOnly 通行证  →  页面内所有请求自动携带
```

- 通行证 Cookie 名：`zf_api_pass`，签名值（HMAC-SHA256），防篡改
- 有效期：24 小时（`API_GATE_PASS_TTL`），过期后刷新页面自动续期
- `HttpOnly`：前端 JS 读不到，XSS 也偷不走
- **前端代码零改动**，所有 `fetch` 走同源，Cookie 自动附带

### 通道 B：脚本 / 第三方集成（带密码）

```bash
# 方式 1：直接带密钥（推荐）
curl -H "X-API-Key: <你的密码>" -X POST http://127.0.0.1:8080/api/chat \
     -H "Content-Type: application/json" \
     -d '{"question":"tp53"}'

# 方式 2：Bearer
curl -H "Authorization: Bearer <你的密码>" ...

# 方式 3：查询参数（不方便加 Header 的场景）
curl "http://127.0.0.1:8080/api/chat?api_key=<你的密码>&..."
```

### 通道 C：用密码换临时票据（票据不暴露密码本身）

```bash
curl -X POST http://127.0.0.1:8080/api/gate/unlock \
     -H "Content-Type: application/json" \
     -d '{"password":"<你的密码>"}'
# → {"ok":true,"token":"v1.1777xxxx.xxxxxxxx","header":"X-API-Key","ttl_seconds":86400}
```

拿到 `token` 后用法同通道 B。适合把密钥分发给短期协作者：**给对方票据，不给密码**，24 小时后自动失效。

---

## 三、白名单（不需要密码）

| 路径 | 原因 |
|---|---|
| `GET /api/health` | 负载均衡 / 监控探活 |
| `GET /api/auth/config` | 前端启动时要读配置 |
| `POST /api/gate/unlock` | 换票据的入口本身 |
| `GET /api/gate/status` | 查询锁状态 |
| `GET /`、`/static/*` | 页面与静态资源 |

其余 `/api/*` 一律校验。

---

## 四、配置项（均在 `.env`）

| 键 | 默认 | 说明 |
|---|---|---|
| `API_GATE_ENABLED` | `0` | 总开关（2026-11-25 起默认关闭）。设 `1` 且配了 `API_GATE_KEY` 才启用 |
| `API_GATE_KEY` | 空 | 访问密码。**为空时锁自动不启用**，服务照常启动 |
| `API_GATE_SECRET` | 随机 48 位 | 通行证签名盐，泄露等于可伪造通行证 |
| `API_GATE_PASS_TTL` | `86400` | 通行证有效期（秒） |
| `API_GATE_BIND_UA` | `0` | 设为 `1` 后通行证绑定浏览器 UA，换浏览器需重新打开页面 |
| `WEB_RATE_LIMIT_GATE` | `8` | 每分钟解锁尝试上限（防密码爆破） |
| `LLM_API_STRICT` | `1` | 上游地址强校验，设 `0` 关闭 |
| `LLM_API_ALLOW_PRIVATE` | `0` | 允许 `http://` 与内网地址（接本地推理服务时设 `1`） |

---

## 五、常见操作

### 换密码

```bash
# 1. 改 .env 里的 API_GATE_KEY
# 2. 重启服务（启动.bat）
# 3. 已发出的通行证会在 TTL 到期后自动失效；想立即失效就改 API_GATE_SECRET 再重启
```

### 临时关闭锁

```bash
# .env
API_GATE_ENABLED=0
```

### 排障：页面能用但脚本 401

```bash
curl -i http://127.0.0.1:8080/api/gate/status      # 看 enabled 与 cookie_name
curl -i -H "X-API-Key: <密码>" http://127.0.0.1:8080/api/health
```

401 响应体会带 `code` 字段：`api_key_required`（没给）、`api_key_invalid`（给错了）。

### 密码试错被封

同一 IP 5 分钟内失败 25 次会封禁 15 分钟（复用原有封禁机制）。解锁接口另有 8 次/分钟的限制。

---

## 六、密钥防护清单（DeepSeek 等外部 API）

| 防护 | 实现位置 | 效果 |
|---|---|---|
| 日志全链路打码 | `core/common_utils.py::redact_secrets` | `sk-***`、`Bearer ***`、`api_key=***`、`password: ***` 一律打码，正常日志不受影响 |
| 上游报错打码 | `llm/llm_api.py` | 上游返回的错误原文先脱敏再抛异常，避免把 key 带进日志/前端 |
| 强制 HTTPS | `_validate_api_base` | `LLM_API_BASE` 非 https 直接拒绝启动，防明文传输 |
| 拒绝内网地址 | `_validate_api_base` | 本机 / 10.x / 192.168.x / 172.16-31.x / 链路本地全部拒绝，防 SSRF |
| 密码不落源码 | 全部模块 | 代码里没有任何明文口令，只从 `.env` / 环境变量读；缺失即报错或降级，不设回退值 |
| 文件权限 | `icacls` | `.env` 仅当前用户 + SYSTEM + Administrators 可读写（原为 Authenticated Users 可改） |
| 弱口令回退移除 | `core/config.py` | `MYSQL_PASSWORD` 默认值由 `<set-via-env>` 改为空，配置丢失时显式失败而非静默用弱口令 |
| 凭据轮换脚本 | `scripts/set_mysql_password.py` | 密码经 `.env` 传递，不进命令行历史 |

---

## 七、安全边界（必须知道）

这层锁的定位是「**防止第三方脚本、爬虫、扫描器直接消耗你的 DeepSeek 额度**」，不是「防住会看网页源码的人」。

原因很直接：页面按你的要求完全开放，任何人打开首页都能拿到通行证。所以真正的成本防线是：

1. **限流**（`WEB_RATE_LIMIT_CHAT=60` / 分钟）
2. **并发车道**（`CHAT_MAX_CONCURRENCY`）
3. **失败封禁**（25 次 / 5 分钟 → 15 分钟）
4. **密钥不出服务端**（`sk-` 只在 `.env`，前端与响应里都没有）

如果将来需要「陌生人必须持密码才能用」，那就得把首页也纳入校验（弹密码框），这会改变现在的免登录体验——两者不可兼得，按需选择。

---

## 八、回滚

```bash
# 只关锁，不动其它
# .env:  API_GATE_ENABLED=0

# 完全回到改之前
# 1. .env: WEB_AUTH_REQUIRED=0、API_GATE_ENABLED=0
# 2. 数据库密码回退：改 .env 的 MYSQL_PASSWORD 后执行
#    python -X utf8 -m scripts.set_mysql_password --dry-run   # 先看
```

---

## 九、验收记录（本次实测）

| 用例 | 期望 | 实测 |
|---|---|---|
| 首页下发通行证 | 200 + `Set-Cookie: zf_api_pass` | 通过 |
| 无凭证直连 `/api/*` | 401 `api_key_required` | 通过 |
| 错误密钥 | 401 `api_key_invalid` | 通过 |
| 正确密钥 | 放行（404 到路由层） | 通过 |
| 浏览器通行证 | 放行 | 通过 |
| 伪造通行证 | 401 | 通过 |
| 解锁：密码正确 | 200 + 46 位票据 | 通过 |
| 解锁：密码错误 | 401 | 通过 |
| 票据当密钥用 | 放行 | 通过 |
| 静态资源 | 不被拦截 | 通过 |
| 真实问答（通行证） | HTTP 200 | 通过（847 字） |
| 真实问答（密钥） | HTTP 200，含基因详情 | 通过（tp53，6110 字，63.6s） |
| 数据库连接 | 27 张表可读 | 通过 |
| 项目自检 | 全部通过 | 9/9 |
