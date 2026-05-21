---
name: differential_transcriptomics_analysis
version: 1
description: 对上传的 RNA-seq counts 表达矩阵进行 DESeq2 差异转录组分析，并生成可点击 HTML 报告
trigger: 用户上传 transcriptomics/RNA-seq counts 基因表达矩阵，要求差异基因分析、差异表达分析、DESeq2 分析、火山图、热图、上调下调基因，或调整 padj/FDR/log2 fold change 阈值后重跑
execution_mode: deterministic_python_r
data_paths: uploaded transcriptomics counts expression matrix
---

# Differential Transcriptomics Analysis Skill

## Contract

- 输入：用户上传的 RNA-seq counts 表达矩阵 CSV/TSV/TXT/XLSX，以及用户当前分析请求。
- 前置：路由前必须先查看 `data_profiles`。只有上传文件 intake 已完成，且被识别为 `data_family=transcriptomics`、`data_type=expression_matrix` 时才调用本 skill。
- 输出：JSON/dict，包含每个比较组的差异基因 summary、输出 CSV 路径、可点击 HTML 报告 URL。

## Behavior

- 该 skill 不使用 LLM 生成分析代码。
- 上传阶段的 Python intake 负责识别分隔符、feature/gene ID 列、数值样本列、样本分组，并生成标准矩阵和样本 metadata。
- R 负责确定性 DESeq2 分析：低 counts 过滤、size factor 归一化、差异表达建模、结果筛选。
- 如果 intake 分组可按同一后缀自动配对，例如 `MT-D/WT-D`、`MT-C/WT-C`、`MT-S/WT-S`，默认输出所有配对比较；用户明确指定两个分组时只跑该比较。
- HTML 报告包含比较组 summary、结果表下载链接、交互式火山图和聚类热图。

## Default Thresholds

- adjusted p-value (`padj`) < 0.05
- `abs(log2FoldChange)` >= 1
- 用户在当前请求中明确给出 `padj`、`FDR`、`pvalue` 或 `log2 fold change` 阈值时，本轮重跑使用该阈值，并在结果与报告中回显实际阈值。

## R Execution

Command-line script: `backend/app/r/differential_transcriptomics.R`.

R 输入固定为 intake 产出的文件：

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
