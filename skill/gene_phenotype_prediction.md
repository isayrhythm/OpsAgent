---
name: gene_phenotype_prediction
version: 1
description: 根据本地 GenePredictor 结果预测水稻或玉米基因可能关联的表型/性状，并按预测分数返回 top-k 条目
trigger: 用户突出询问基因表型预测、可能表型、可能性状、预测某个基因与哪些性状相关、phenotype prediction、trait prediction；例如“预测 Zm00001eb123456 的表型”“LOC_Os07g48050 可能跟哪些性状相关”
execution_mode: deterministic_python
executor: gene_phenotype_prediction
argument_resolver: message
input_schema: skill/schemas/gene_phenotype_prediction.input.json
output_schema: skill/schemas/gene_phenotype_prediction.output.json
data_paths: data/GenePredictor/maize_lte_result.parquet, data/GenePredictor/rice_lte_result.parquet, data/GenePredictor/maize_lte_result.csv, data/GenePredictor/rice_lte_result.csv, data/gene_info/maize_gene_trans.json, data/gene_info/rice_gene_trans.json
---

# Gene Phenotype Prediction Skill

## Contract

- 输入：用户自然语言请求，通常包含水稻或玉米基因 ID、旧 ID、别名或 gene symbol。
- 数据源：优先读取 `data/GenePredictor/maize_lte_result.parquet` 和 `data/GenePredictor/rice_lte_result.parquet`；如果 Parquet 不存在，再回退读取同名 CSV。
- 映射：复用基因查询数据中的 `maize_gene_trans.json` 和 `rice_gene_trans.json` 将别名映射到标准 ID。
- 输出：JSON/dict，必须包含命中的基因、物种、top-k 表型预测和预测分数。

## Behavior

- 该 skill 不使用 LLM 生成查询代码。
- 仅当用户明确在问“预测/可能关联表型/可能关联性状”时使用；普通基因注释、功能解释、位置、GO、KEGG 查询仍使用 `query_gene_info`。
- 默认返回预测分数最高的前 5 个性状；如果用户明确说 top-k、前 N 个或 N 个性状，则使用用户指定数量，最大 50。
- 如果用户明确物种，只查对应物种；如果没有明确物种，先用水稻和玉米的基因映射解析，命中哪个物种就查哪个物种。
- 不对全表做模糊文本搜索，只按解析出的标准基因 ID 过滤 `gene_id`。

## Result Shape

```json
{
  "status": "completed",
  "analysis": "gene_phenotype_prediction",
  "query": "预测 LOC_Os07g48050 的表型 top 5",
  "top_k": 5,
  "species_searched": ["rice"],
  "genes": ["AGIS_Os07g..."],
  "gene_mappings": [
    {
      "input": "LOC_Os07g48050",
      "species": "rice",
      "species_label": "水稻",
      "canonical_id": "AGIS_Os07g...",
      "query_id": "agis_os07g...",
      "matched_by": "gene_trans"
    }
  ],
  "matches": [
    {
      "input": "LOC_Os07g48050",
      "species": "rice",
      "species_label": "水稻",
      "canonical_id": "AGIS_Os07g...",
      "query_id": "agis_os07g...",
      "matched_by": "gene_trans",
      "top_k": 5,
      "predictions": [
        {
          "rank": 1,
          "phenotype": "grain_number_per_panicle",
          "pred_score": 0.8
        }
      ],
      "source_file": "data/GenePredictor/rice_lte_result.csv"
    }
  ],
  "not_found": []
}
```
