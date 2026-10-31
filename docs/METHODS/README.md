# METHODS 目录

**正文只放散文**（2.1–2.8，与投稿稿一致）；一切工程性内容（写作口径、归属声明、数字对账表、待补清单）
都收在 `_provenance.md`，不出现在正文里。文中每个数字均由脚本从仓库产物重算，不以手写为准。

| 文件 | 角色 | 怎么来的 |
| --- | --- | --- |
| `METHODS.md` | **正文**（2.1–2.8 散文，`{{N:key}}` 占位符）。要改内容只改这里 | 手写 |
| `METHODS.resolved.md` | **投稿版**：占位符已替换为实测值，无任何表格/清单 | 由下面命令生成，**不要手改** |
| `_provenance.md` | **内部记录**（不属正文）：写作口径、归属声明、数字对账表、待补清单 | 手写，供对账脚本读取 |

## 命令

```bat
rem 重算全部数字、渲染投稿版，并与内部记录里的对账表逐条比对
.venv\Scripts\python.exe -X utf8 scripts\verify_methods_numbers.py --write

rem 全仓自检（含 METHODS 数字对账、数据包指纹、前端语法等 8 项）
.venv\Scripts\python.exe -X utf8 scripts\selfcheck.py --git
```

## 相关文件（不在此目录）

- `docs/paper/BACK_MATTER.md` — 尾段（摘要、参考文献、致谢等），与 METHODS 配套，投稿时拼在其后
- `docs/paper/_potato_methods_clean.txt` — 叙述结构的写作参照（第三方原文，不入提交）
- `docs/paper/CLAIM_AUDIT.md` — 「METHODS 声明 ↔ 实现」一致性审计
- `Figure/supplementary/` — 补充图与图注来源
