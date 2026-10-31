# Back matter（尾段）— 与马铃薯同序

> 本文件与 `docs/METHODS/METHODS.md` 配套：**METHODS 只放方法**，其余"尾段"独立在此，投稿时按下面顺序拼进正文之后。
> 数值口径同 METHODS：能确定的写实，缺外部编号的写 `{{TODO:key}}`，**不编造**。

## 在整篇稿件中的位置（Cell Press / Plant Communications 同序）

```
Title page（题目 / 作者 / 单位 / 通讯）
Abstract
Introduction
Results
Discussion
Methods                      ← docs/METHODS/METHODS.md（2.1–2.8）
DATA AND CODE AVAILABILITY   ← 本文件 ①  ← 马铃薯同序起点
FUNDING                      ← 本文件 ②
ACKNOWLEDGMENTS              ← 本文件 ③（含 "No conflict of interest is declared."）
AUTHOR CONTRIBUTIONS         ← 本文件 ④
SUPPLEMENTAL INFORMATION     ← 本文件 ⑤
References
```

> 说明：Cell Press 要求"生成式 AI 参与写作"必须披露，因此 Methods 里多了一节
> `2.6 Use of AI-assisted technologies`（马铃薯原文没有这一节）；声明放在 Methods 内，
> 尾段 5 节仍与马铃薯保持完全同序同名。

---

## DATA AND CODE AVAILABILITY  <!-- Owner: Kaifeng Qu -->

The code for the Zebrafish Knowledge Hub (agent orchestration, retrieval and packing
pipeline, web front-end and deployment scripts) will be released upon publication. Benchmark
questions, manually curated reference answers, blinded judge inputs and raw model outputs,
scoring tables and the exact permutation-test results are available on Figshare
({{TODO:data_deposit}}). Reference annotations were downloaded
from Ensembl (GRCz11, release 116; https://www.ensembl.org/), NCBI RefSeq (GRCz12tu,
GCF_049306965.1; https://www.ncbi.nlm.nih.gov/datasets/genome/GCF_049306965.1/), ZFIN
(https://zfin.org/), Bgee (v15.2; https://www.bgee.org/) and the EBI Expression Atlas
(E-ERAD-475; https://www.ebi.ac.uk/gxa/), and literature records were retrieved from
PubMed (https://pubmed.ncbi.nlm.nih.gov/) and Europe PMC (https://europepmc.org/).

## FUNDING  <!-- Owner: Kaifeng Qu -->

This work was supported by {{TODO:funding}}.

<!-- 对照马铃薯写法：逐一列「项目名 + 项目号」，逗号分隔，最后以句点结束。马铃薯原文示例：
"This work was supported by the Guangdong Major Project of Basic and Applied Basic Research
(2021B0301030004), the National Natural Science Foundation of China (32488302, 32302582,
and 32402608), the Agricultural Science and Technology Innovation Program (CAAS-ZDRW202404),
the Yunnan Fundamental Research Projects (grants 202501BC070003 and 202501AT070005), the
China Postdoctoral Science Foundation (BX20200376), and the Yunnan Province Xingdian Talent
Support Program."
我们这边常见可填项（有哪个写哪个，无则删）：
- the National Natural Science Foundation of China (NSFC)（项目号）
- the Natural Science Foundation of Hunan Province（项目号）
- the Scientific Research Foundation of Hunan Normal University（项目号）
- 国家重点研发计划 / 湖南省重点研发计划 / 重点实验室开放基金（项目号）
-->

## ACKNOWLEDGMENTS  <!-- Owner: Kaifeng Qu -->

We acknowledge the computing resources of Hunan Normal University used for index
construction and model serving, and we thank the researchers who tested the platform for
performance and stability and provided constructive feedback. No conflict of interest is
declared.

<!-- 马铃薯原文对应句："We acknowledge technical support from the Intelligent Computing Center of
Yunnan Normal University. We thank ... All researchers who participated in platform testing ...
No conflict of interest is declared."
若你们有具体致谢对象（如超算中心、测试同学姓名），按马铃薯那样点名即可。
-->

## AUTHOR CONTRIBUTIONS  <!-- Owner: 全部作者确认 -->

Kaifeng Qu conceived the study; developed the code of the Zebrafish Knowledge Hub (agent
orchestration framework, web front-end and back-end, initial creation of the relational
database, full-text parsing, and the vectorisation and retrieval pipeline); performed the
performance evaluation; and wrote the manuscript with the assistance of AI coding
environments as declared in section 2.6. \<Author B\> collected and curated the omics
annotation data (gene coordinates, expression, GO and pathway annotations). All authors read
and approved the final version.

<!-- 马铃薯原文写法（供对照）："S.H., Y.Z., and Yuxin Jia conceived the study. Yuxin Jia and Y.Z.
wrote the manuscript. Yudong Jia, Yuxin Jia, and J. Li developed the code ... J. Li performed the
bioinformatics analyses. ... All authors read and approved the final version."
我们目前是"单人主导 + 1 位组学数据合作者"，等 Author B 姓名/单位确认后把 \<Author B\> 替换掉即可。
-->

## SUPPLEMENTAL INFORMATION  <!-- Owner: Kaifeng Qu -->

Supplemental information includes the pipeline and benchmark figures, the API-usage table
(Table 2.1 / supplementary version) and the audited numeric-provenance tables that link every
reported value to the underlying project artefact.

---

## 待补清单（只剩外部门户信息）

| 占位符 | 需要什么 | 马铃薯对应 |
| --- | --- | --- |
| 代码仓库（原 `TODO:repo_url` 占位符） | **已定：暂不公开**，正文已写 "The code will be released upon publication."；正式 Gitee/GitHub 地址待公开时回填 | `https://gitee.com/yuxin_2023/potato-knowledge-hub` |
| `{{TODO:data_deposit}}` | Figshare DOI（我可以先把待上传数据包打好，你上传后回填） | `https://doi.org/10.6084/m9.figshare.29815859` |
| `{{TODO:funding}}` | 基金项目名 + 项目号（**不可编造**） | 见上方注释中的马铃薯原文 |
| `\<Author B\>` | 组学数据合作者姓名 + 单位 | 马铃薯列了 11 位参与者 |
| `\<email\>` | 通讯邮箱 | — |

对账：`.venv\Scripts\python.exe -X utf8 scripts\verify_methods_numbers.py --write`
（脚本会同时检查本文件与 `METHODS.md` 的占位符状态，并重算 METHODS 中全部数字）
