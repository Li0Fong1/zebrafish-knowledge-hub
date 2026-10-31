# 发布安全审计（2026-10-05）

> ⚠️ 本文件内的主机名/密码均以脱敏写法给出（如 `kt[.]whcxd[.]cn`、`deng@20xx`），以免本文件自身触发 `scripts/selfcheck.py` 的凭据扫描。

> 目的：确保这个仓库**可以公开**——不泄露服务器凭据，不误带大数据，且"拟提交清单"可复验。
> 复验命令：`.venv\Scripts\python.exe scripts\selfcheck.py --git`（第 6、7 项即本审计的自动化版本）

## 一、审计方法

1. 用**临时 git 目录**（`--git-dir=<tmp> --work-tree=<repo>`）执行 `git add -A --dry-run`，
   得到不受 `git status` 缓存干扰的真实"拟提交清单"，**不在工作区创建 `.git`**。
2. 对清单内每个文本文件做正则扫描：`kt[.]whcxd[.]cn`（主机名）、`deng@20xx`（真实密码）、
   `公网密码`、`password="…"`。
3. 另行全仓扫描（排除已被 `.gitignore` 覆盖的目录），确认源码区无残留。

## 二、发现与处置

| # | 发现 | 严重度 | 处置 |
| --- | --- | --- | --- |
| 1 | **5 个脚本硬编码服务器密码**：`scripts/audit_fig9_10_deep.py`、`audit_figures_vs_project.py`、`collect_nanog_evidence.py`、`hub_tunnel.py`、`probe_retrieval_health.py`，形如 `PWD = "…"` | 🔴 高 | 全部改为 `_zf_pass()`：**只从环境变量或仓库根 `.env` 读取**，缺失即明确报错；源码内不再出现密码 |
| 2 | `scripts/probe_tool_versions.py`、`push_web.py` 把密码写成 `os.environ.get(..., "…")` 的**兜底默认值** | 🔴 高 | 同上，改为必填（无兜底）；`.env` 补 `ZF_HOST/ZF_PORT/ZF_USER/ZF_PASS/ZF_BASE/ZF_RUNTIME` |
| 3 | `scripts/sync_server_data_incremental.py` 硬编码主机/端口/账号 | 🟠 中 | 改为 `_zf_host()/_zf_port()/_zf_user()/_zf_pass()` |
| 4 | `.gitignore` 仅 11 行，无法阻止敏感目录入库（实测 `项目阶段/` 有 **558** 个文件含主机/账号痕迹，`logs/` 141 个） | 🔴 高 | 重写为 5 组规则：①凭据/敏感目录 ②大数据与模型 ③日志/一次性脚本/备份 ④本机环境缓存 ⑤保留历史规则 |
| 5 | 3 个溯源 JSON 内含 `主机:端口` 与 `/data/zoujin/deng/…` 账号路径（`Figure/_server_env_probe.json`、数据包 `05_provenance/server_env_probe.json`、`tool_versions.json`） | 🟠 中 | 脱敏为 `<lab-server>` / `<server>/…`，并写入 `_redaction` 说明；**版本事实（OS/Python/BLAST/MySQL）完整保留** |
| 6 | `docs/` 内 5 处服务器绝对路径 | 🟡 低 | 规范化为 `<BASE>/`、`<RT>/` 占位符 |
| 7 | `.env.example` 含真实域名/端口 | 🟠 中 | 改为纯占位（`your.server.host` / `22` / `your_user` / `your_password`） |
| 8 | 数据包 `--check` 只校验已收录项，**不检测"包内多出文件"**（`tool_versions.json` 因此长期漏检） | 🟠 中 | `build_figshare_package.py --check` 新增 `EXTRA-NOT-IN-MANIFEST` 检测；产物源移出包目录→`docs/paper/provenance/` 并纳入 `COPY_MAP` |
| 9 | 仓库根散落备份：`api/api_server.py.bak.$(date +%s)`、`core/common_utils.py.broken.bak`、`core/_restored_config_314.py` | 🟡 低 | 移入 `项目阶段/04_运维静音自愈/归档/一次性脚本与备份_20261005/`（可回滚） |
| 10 | 无 `LICENSE`（Code availability 不给许可即默认"保留所有权利"） | 🟠 中 | 新增 MIT `LICENSE`（Copyright (c) 2026 Kaifeng Qu and contributors，可改） |

## 三、终态（实测）

```
[PASS] secrets   — 源码区命中 0 个文件（无服务器主机名/密码）
[PASS] git-plan  — 拟提交 665 文件 / 46.3 MB；无敏感命中
```

- 主机名残留：**0**
- 真实密码残留：**0**
- 服务器绝对路径：**228 处**，分布于 `scripts/`(79)、`Figure/`(67)、`deploy/`(44)、`_server_web/`(32, 已忽略)、`docs/`(0, 已规范化)、`database/`(1)
  → **接受**：这些是**服务器上功能性路径**（例如 `deploy/hub_guard.sh` 就靠 `BASE=<RT 同级目录>` 运行，改了会直接弄坏自愈脚本）。
  它们不是凭据；如你希望一并隐去，需要同步修改服务器上的脚本，风险高，故未动。

## 四、残留风险与建议

1. **密码轮换**：`deng@20xx` 曾以明文形式存在于多个脚本、`.env`、`密码箱/公网密码.txt` 以及本机编辑器历史里。
   即使现在源码已清干净，**建议在服务器上改一次密码**，并确认它没有进过任何共享位置。
2. `.env` 与 `密码箱/` 已被 `.gitignore` 排除，但**首次提交前请再确认**：
   `git status --porcelain | findstr /R "\.env 密码箱"` 应无输出。
3. `deploy/` 下的 shell 脚本含服务器布局，属运维材料；若仓库面向公众，可考虑把 `deploy/` 一并排除或做同样的占位化处理。
