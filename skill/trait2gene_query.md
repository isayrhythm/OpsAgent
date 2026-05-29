---
name: trait2gene_query
version: 1
description: 当用户询问某个性状、表型、抗性、农艺性状、生理性状或胁迫耐受性相关的基因有哪些时触发；本 skill 查询本地 trait2gene 数据库，返回与性状分类相关的基因、来源和文献
trigger: 性状相关基因查询、trait to gene、trait2gene、哪些基因影响某性状、某性状相关基因、抗旱相关基因、耐盐相关基因、株高相关基因、籽粒大小相关基因、某表型有哪些候选基因；如果用户询问“某个基因可能关联哪些性状/表型”，应使用 gene_phenotype_prediction，而不是本 skill
execution_mode: deterministic_python
executor: trait2gene_query
argument_resolver: message
input_schema: skill/schemas/trait2gene_query.input.json
output_schema: skill/schemas/trait2gene_query.output.json
data_paths: data/trait2gene/ath_trait2gene_paper_tair_3.csv, data/trait2gene/genedb_172traitCategroy_v3.csv, data/trait2gene/maize_db_trait158_v5.csv, data/trait2gene/SoyGeneDB_trait164_v2.csv
---

# Trait2Gene Query Skill

## Contract

- 输入：用户自然语言请求，通常描述一个或多个性状/表型，例如耐盐、抗旱、株高、籽粒大小、开花期、抗病性。
- 分类：执行器会先调用 LLM，从各物种数据表的 `classify2` 候选分类中选择匹配项；LLM 只能选已有分类，不能创造新分类。
- 查询：根据选中的 `classify2` 在本地 trait2gene CSV 中确定性查询；如果选择多个分类，则返回同时关联这些分类的基因交集。
- 数据源：
  - Arabidopsis: `data/trait2gene/ath_trait2gene_paper_tair_3.csv`
  - rice: `data/trait2gene/genedb_172traitCategroy_v3.csv`
  - maize: `data/trait2gene/maize_db_trait158_v5.csv`
  - soybean: `data/trait2gene/SoyGeneDB_trait164_v2.csv`
- 输出：JSON/dict，包含 LLM 选择的分类、命中的物种、总基因数、返回的 top genes、证据来源和参考文献。
- 最终回答必须优先展示工具返回的 `evidence` / `references`；如果工具结果没有文献字段，不得自行编造文献标题、作者、年份或 DOI。

## Behavior

- 本 skill 回答“性状 -> 基因”问题。
- “基因 -> 可能性状/表型预测”问题应使用 `gene_phenotype_prediction`。
- 用户必须明确给出物种；如果用户没有说明物种，应先请用户补充物种，不要跨物种直接查询。
- 如果用户明确给出物种，只返回该物种结果。
- 默认返回每个物种最多 20 个高证据基因；如果用户明确要求 top-k/前 N 个，最多返回 100 个。
- 宽泛上位性状映射到多个 `classify2` 时使用并集，例如“大豆产量相关基因”可映射到百粒重、单株荚数、单荚粒数、植株重量等，返回任一分类相关的基因。
- 只有用户明确要求“同时包含/共同影响/兼具”多个性状时，才使用交集。
- 查询不到时必须说明“未在当前 trait2gene 数据库中检索到”，不能编造基因。

## Result Shape

```json
{
  "status": "completed",
  "analysis": "trait2gene_query",
  "query": "水稻耐盐相关基因有哪些？",
  "classification": {
    "selected": [
      {
        "species": "rice",
        "categories": ["soil salinity tolerance"]
      }
    ],
    "top_k": 20,
    "reason": "用户询问水稻耐盐相关基因"
  },
  "matches": [
    {
      "species": "rice",
      "species_label": "rice",
      "categories": ["soil salinity tolerance"],
      "total_genes": 100,
      "returned_genes": 20,
      "genes": [
        {
          "gene_id": "AGIS_Os...",
          "gene_names": ["..."],
          "categories": ["soil salinity tolerance"],
          "evidence_count": 3,
          "sources": ["RAP-DB"],
          "references": ["..."],
          "evidence": [
            {
              "category": "soil salinity tolerance",
              "trait": "...",
              "literature": "...",
              "source": "literature"
            }
          ],
          "trait_examples": ["..."]
        }
      ],
      "source_counts": [
        {
          "source": "RAP-DB",
          "unique_genes": 10
        }
      ],
      "references": ["..."],
      "source_file": "data/trait2gene/genedb_172traitCategroy_v3.csv"
    }
  ],
  "not_found": []
}
```
