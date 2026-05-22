---
name: differential_protein_analysis
version: 1
description: 对上传的蛋白组定量矩阵进行差异蛋白分析，并生成可点击 HTML 报告
trigger: 用户上传 proteomics/protein 蛋白组定量矩阵，要求差异蛋白分析、差异表达分析、火山图、热图、上调下调蛋白、DIA-NN protein matrix 分析，或调整 pvalue/fold change 阈值后重跑
execution_mode: deterministic_python_r
executor: differential_protein_analysis
argument_resolver: differential_analysis_json
input_schema: skill/schemas/differential_protein_analysis.input.json
output_schema: skill/schemas/differential_protein_analysis.output.json
data_paths: uploaded proteomics expression matrix
---

# Differential Protein Analysis Skill

## Contract

- 输入：用户上传的蛋白组定量矩阵 CSV/TSV/XLSX，以及用户当前分析请求。
- 前置：路由前必须先查看 data_profiles。只有上传文件 intake 已完成，且被识别为 `data_family=proteomics`、`data_type=expression_matrix` 时才调用本 skill。
- 输出：JSON/dict，包含每个 comparison 的差异分析 summary、输出 CSV 路径、可点击 HTML 报告 URL。

## Behavior

- 该 skill 不使用 LLM 生成分析代码。
- 上传阶段的 Python intake 已负责识别分隔符、表头、蛋白注释列、数值样本列、样本分组，并生成标准矩阵。
- R 负责确定性差异分析：均值、fold change、log2 fold change、Welch t-test、BH 校正、上调/下调判定。
- HTML 报告包含 comparison selector、summary、火山图、热图和结果表下载链接。
- 两个分组时默认执行单个 comparison。三个及以上分组若能按名称自动配对，例如 `MT-D/WT-D`、`MT-C/WT-C`，默认执行这些配对；否则用户请求必须明确比较关系，例如 `MT1 vs WT` 和 `MT2 vs WT`。

## Default Thresholds

- pvalue < 0.05
- fold_change >= 1.5 记为 up
- fold_change <= 2/3 记为 down
- 用户在当前请求中明确给出 `pvalue` 或 `fold change` 阈值时，本轮重跑使用该阈值，并在 summary/report 中回显实际阈值。

## R Execution

Command-line script: `backend/app/r/differential_protein.R`.

R 输入固定为 intake 产出的矩阵、样本 metadata 和 executor 生成的比较表：

- `standard_matrix.csv`：前三列为 `feature_id`、`feature_name`、`description`，其余列为样本定量值。
- `sample_metadata.csv`：两列为 `sample`、`condition`。
- `comparisons.csv`：每行一个比较，包含 `slug`、`comparison`、`numerator`、`denominator`。

执行规则：

```r
matrix <- read.csv(matrix_file, check.names = FALSE, stringsAsFactors = FALSE)
metadata <- read.csv(metadata_file, check.names = FALSE, stringsAsFactors = FALSE)
comparisons <- read.csv(comparisons_file, check.names = FALSE, stringsAsFactors = FALSE)
samples_numerator <- metadata$sample[metadata$condition == numerator]
samples_denominator <- metadata$sample[metadata$condition == denominator]

values_numerator <- as.numeric(unlist(matrix[i, samples_numerator, drop = FALSE]))
values_denominator <- as.numeric(unlist(matrix[i, samples_denominator, drop = FALSE]))
mean_numerator <- mean(values_numerator, na.rm = TRUE)
mean_denominator <- mean(values_denominator, na.rm = TRUE)
fold_change <- mean_numerator / mean_denominator
log2_fc <- log2(fold_change)
pvalue <- t.test(values_numerator, values_denominator)$p.value
padj <- p.adjust(pvalue, method = "BH")
regulation <- ifelse(pvalue < pvalue_cutoff & fold_change >= fold_change_cutoff, "up",
  ifelse(pvalue < pvalue_cutoff & fold_change <= (1 / fold_change_cutoff), "down", "not_significant"))
```

产出固定为：

- `{slug}_all_results.csv`
- `{slug}_differential_results.csv`
- `{slug}_up_results.csv`
- `{slug}_down_results.csv`
- `report.html`
