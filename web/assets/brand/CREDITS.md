# 图片与素材署名 / Image Credits

本目录下的品牌素材若标注为自有，均由本项目自绘或程序化生成；其余按下列许可使用。

## 首页背景图 `zf-home-bg.jpeg`

**当前线上版本：AI 生成的水下实景（2026-10-24 接入）**（项目自有素材，无第三方授权要求）

- 原图：`屏幕截图 2026-10-22 175207.png`（1969×1172），由项目负责人用**豆包**生成后提供
- 内容：水下逆光场景 —— 顶部光束、两侧水草与岩石、沙床、3 条斑马鱼、右上角蓝色 DNA 双螺旋 + 网络节点
- 处理：`scripts/remove_doubao_watermark.py` 擦掉右下角"豆包AI生成"水印 →
  `scripts/apply_home_bg.py` 按 16:10 裁切、提亮 6%、降饱和 4%、轻度锐化，输出 2560×1584
- 水印擦除只替换笔画像素（大窗口中值估背景 + 羽化回贴），岩石/水草结构原样保留

### 换图 / 重新出图

提示词包见 `docs/背景图生成提示词.md`（11 项特征拆解 + 中/英提示词 + 负向词 + 参数 + 验收清单）。
新图（尤其带工具水印的）一条命令上线：

```bash
.venv\Scripts\python.exe -X utf8 scripts\remove_doubao_watermark.py --src "D:\下载\bg.png"
.venv\Scripts\python.exe -X utf8 scripts\apply_home_bg.py --src "D:\下载\bg_nowm.png"
```

### 备选方案

| 方案 | 命令 | 说明 |
| --- | --- | --- |
| 程序化插画 | `scripts/make_home_bg_art.py` | 浅蓝渐变+水草+斑马鱼+DNA，可直接重生成 |
| 真实水下照片 | `scripts/make_home_bg_photo.py` | Wikimedia Commons，CC BY 3.0 **需署名**（见下） |

- 原作：**Forests Underwater (122868199)**
- 作者：**Georg Pirrung**
- 来源：<https://commons.wikimedia.org/wiki/File:Forests_Underwater_(122868199).jpeg>
- 许可：**CC BY 3.0** — <https://creativecommons.org/licenses/by/3.0/>
- 修改：等比裁切、轻度降饱和与提亮、顶部白色渐变遮罩、锐化

> ⚠️ 若切到照片版，CC BY 3.0 要求保留署名 —— 需同时在站点 About 弹层给出作者与许可链接。

## 生成脚本

| 脚本 | 用途 |
| --- | --- |
| `scripts/apply_home_bg.py` | **一条命令上线**：裁切 → 调色 → 覆盖 → 自动升 `?v=` → 推送 |
| `scripts/remove_doubao_watermark.py` | 擦掉豆包/即梦压在角落的"XXAI生成"水印（笔画级替换，不糊结构） |
| `scripts/make_home_bg_clean.py` | 备选：极简浅蓝渐变（2026-10-22 过渡期曾上线） |
| `scripts/make_home_bg_art.py` | 备选：程序化插画（水草/斑马鱼/DNA） |
| `scripts/make_home_bg_photo.py` | 备选：Commons 真实水下照片 → 裁切/调色/遮罩 |
| `scripts/gen_bg_pollinations.py` | 在线生图（Pollinations/Flux）。免费匿名调用已 402，需 `POLLINATIONS_TOKEN` |
| `scripts/make_home_bg_v3.py` | 旧方案：修补原素材图的磨砂面板（已停用） |
| `scripts/find_bg_image.py` | 在 Commons 检索自由授权候选图并下载预览 |

## 其他

- `zf-ai-mark.png` / `zf-ai-frame.png` / `zf-agent-mark.png` / `zf-agent-emblem.png` / `favicon.png` / `apple-touch-icon.png`
- `ocean-scene.svg` / `agent-scene.svg` / `agent-bg.svg`

以上为本项目自有素材（自绘 / 程序生成），无第三方授权要求。
