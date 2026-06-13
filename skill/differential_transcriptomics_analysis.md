---
name: differential_transcriptomics_analysis
version: 1
description: 对上传的 RNA-seq counts 表达矩阵进行 DESeq2 差异转录组分析，并生成可点击 HTML 报告
trigger: 用户上传 transcriptomics/RNA-seq counts 基因表达矩阵，要求差异基因分析、差异表达分析、DESeq2 分析、火山图、热图、上调下调基因，或调整 padj/FDR/log2 fold change 阈值后重跑
execution_mode: deterministic_python_r
executor: differential_transcriptomics_analysis
argument_resolver: differential_analysis_json
input_schema: skill/schemas/differential_transcriptomics_analysis.input.json
output_schema: skill/schemas/differential_transcriptomics_analysis.output.json
data_paths: uploaded transcriptomics counts expression matrix
answer_requirements: If report_url is present, always include it as a Markdown link named HTML report.; Mention every analyzed comparison and the padj/log2FoldChange thresholds actually used.; Summarize significant/up/down gene counts for each comparison when available.; If report_url is missing, explicitly say the HTML report was not generated and include available CSV output paths instead.; Do not invent plots, comparisons, thresholds, or output files that are not present in the skill result.
---

# Differential Transcriptomics Analysis Skill

## Contract

- 输入：用户上传的 RNA-seq counts 表达矩阵 CSV/TSV/TXT/XLSX，以及用户当前分析请求。
- 前置：路由前查看 File Inspector 生成的通用 `data_profiles`；真正的 counts 表达矩阵 schema 校验、样本列识别、分组和标准矩阵生成由内置 File Transformer 根据本 skill 的说明和输入契约在执行前完成。
- 输出：JSON/dict，包含每个比较组的差异基因 summary、输出 CSV 路径、可点击 HTML 报告 URL。

## Behavior

- 该 skill 不使用 LLM 生成分析代码。
- File Transformer 负责根据本 skill 的说明和输入契约识别分隔符、feature/gene ID 列、数值样本列、样本分组，并生成标准矩阵和样本 metadata。
- R 负责确定性 DESeq2 分析：低 counts 过滤、size factor 归一化、差异表达建模、结果筛选。
- 如果第一次 DESeq2 执行失败，调用层只允许白名单修复：删除含非数值、缺失、负数或全零 counts 的基因行，将 counts 四舍五入为整数，然后使用同一个 R 脚本重跑一次；不会修改 DESeq2 主脚本或统计口径。
- 如果 intake 分组可按同一后缀自动配对，例如 `MT-D/WT-D`、`MT-C/WT-C`、`MT-S/WT-S`，默认输出所有配对比较；用户明确指定两个分组时只跑该比较。
- HTML 报告包含比较组 summary、结果表下载链接、交互式火山图和聚类热图。

## Default Thresholds

- adjusted p-value (`padj`) < 0.05
- `abs(log2FoldChange)` >= 1
- 用户在当前请求中明确给出 `padj`、`FDR`、`pvalue` 或 `log2 fold change` 阈值时，本轮重跑使用该阈值，并在结果与报告中回显实际阈值。

## R Execution

Command-line script: `backend/app/r/differential_transcriptomics.R`.

R 输入固定为 File Transformer 产出的文件：

- `standard_matrix.csv`：前三列为 `feature_id`、`feature_name`、`description`，其余列为 counts 样本列。
- `sample_metadata.csv`：两列为 `sample`、`condition`。
- `comparisons.csv`：每行给出 `numerator`、`denominator` 和输出文件 `slug`。

执行规则基于 DESeq2：

```r
dds <- DESeqDataSetFromMatrix(countData = counts_matrix, colData = metadata, design = ~ condition)
keep <- rowSums(counts(dds) >= 10) >= min(3, ncol(dds))
dds <- DESeq(dds[keep, ], fitType = "mean", minReplicatesForReplace = 7, parallel = FALSE)
res <- results(dds, contrast = c("condition", numerator, denominator), alpha = padj_cutoff)
significant <- subset(res_df, padj < padj_cutoff & abs(log2FoldChange) >= log2_fc_cutoff)
```

每个比较固定产出：

- `{comparison}_all_genes.csv`
- `{comparison}_significant_genes.csv`
- `{comparison}_up_genes.csv`
- `{comparison}_down_genes.csv`

整轮分析额外产出：

- `normalized_counts.csv`
- `report.html`

若触发可控重试，会额外产出：

- `retry_standard_matrix.csv`
- `retry_plan.json`
