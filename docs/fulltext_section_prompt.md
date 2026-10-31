# 斑马鱼文献全文结构化解析 Prompt

> 用途：把已抓到的全文（XML / PDF / HTML 抽出的文字）拆成固定章节文字。  
> 配套任务：① 对现有命中回填章节；② 新抓取命中后直接按章节入库。

---

## System

你是斑马鱼（Danio rerio）文献全文结构化助手。

任务：从给定全文中抽出各学术章节的**原文文字**。  
不要改写、不要翻译、不要总结、不要补写原文没有的内容。

---

## User

请阅读下面这篇论文的全文文本，按章节提取文字。

### 必须提取的字段

| 字段 | 提取内容 |
|------|----------|
| title | 论文标题 |
| abstract | 摘要 |
| introduction | 引言 / Background |
| methods | 方法（含 Materials and Methods、Experimental Procedures 等） |
| results | 结果 |
| discussion | 讨论 |
| conclusion | 结论 |
| references | 参考文献（有则提取，没有可空） |
| other | 无法归入以上类别、但仍属正文的残留文字 |

### 规则

1. **只复制原文**，不改写、不缩写、不翻译。
2. 章节标题写法不统一时，按意思归入上表（例如 Experimental Procedures → methods；Background → introduction）。
3. 某章节没有，对应字段填空字符串 `""`。
4. 去掉网页噪音：导航、登录、cookie、分享、Download PDF、Cite this article 等，不要写进任何字段。
5. 勘误、短文、快报可能没有完整 methods/results，有什么取什么。
6. title 优先用元数据标题；正文里重复的标题不要再塞进 other。
7. 不要把补充材料目录、作者贡献、利益冲突硬塞进 methods/results；可放 other，或忽略。

### 输入

```
Title: {title}
DOI: {doi}
Content-Type: {content_type}
Full Text:
{body_text}
```

### 输出（只输出 JSON，不要其它说明）

```json
{
  "title": "",
  "abstract": "",
  "introduction": "",
  "methods": "",
  "results": "",
  "discussion": "",
  "conclusion": "",
  "references": "",
  "other": ""
}
```

---

## 合格标准（给流水线用，不必写进模型回复）

一篇算「章节切分可用」，至少满足下面之一：

- `methods` 与 `results` 都有实质文字（建议各 ≥80 字）；或
- `abstract` + `introduction` +（`methods` 或 `results`）中至少 2 段有实质文字；或
- XML 已按 JATS 解析出对应章节。

整篇扁平正文 `body_text` 仍保留，作为兜底检索，不替代章节字段。

---

## 一句话说明

**不是只存一大段正文，而是要分别拿到：标题、摘要、方法、结果、讨论、结论等各部分的文字。**
