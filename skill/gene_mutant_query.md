---
name: gene_mutant_query
version: 1
description: 查询本地突变体数据库，判断某个基因是否有 ABRC/NASC、BGBIO 或 Maize EMS 突变体材料；这是突变体材料、突变体种子、stock、T-DNA、EMS 和编辑突变位点查询的专用 skill，优先级高于通用基因信息查询。
trigger: 用户询问某个基因有没有突变体、有没有突变体种子、哪里可以买突变体材料、T-DNA insertion line、ABRC/NASC stock、EMS mutant、编辑突变体、突变材料、突变位点、下单购买突变体；例如“LOC_Os04g54860 有突变体吗？”“LOC_Os05g47780 有突变体种子吗？”“AT2G30410 有没有 T-DNA 突变体种子？”“Zm00001eb000010 的 EMS 突变体”
execution_mode: deterministic_python
executor: gene_mutant_query
argument_resolver: message
input_schema: skill/schemas/gene_mutant_query.input.json
output_schema: skill/schemas/gene_mutant_query.output.json
data_paths: data/mutant_db/ath_abrc.parquet, data/mutant_db/rice_bgbio.parquet, data/mutant_db/maize_ems.parquet, data/mutant_db/ath_gene_trans.json, data/mutant_db/rice_gene_trans.json, data/mutant_db/soy_gene_trans.json, data/mutant_db/maize_gene_trans.json
---

# Gene Mutant Query Skill

## Contract

- 输入：用户自然语言请求，通常包含基因 ID、旧 ID、别名或 gene symbol。
- 查询目标：判断基因在本地突变体数据库中是否有突变体/种子/stock/EMS/编辑材料记录。
- 映射：先用 `data/mutant_db/*_gene_trans.json` 将用户输入标准化到对应数据库可查 ID。
- 查询：只按标准化后的基因 ID 精确过滤数据库主键字段，不做全表模糊文本搜索。
- 输出：JSON/dict，必须包含 `analysis`, `gene_mappings`, `matches`, `not_found`。

## Databases

- Arabidopsis: `data/mutant_db/ath_abrc.parquet`, query column `gene_id`.
- Rice: `data/mutant_db/rice_bgbio.parquet`, query column `基因号`.
- Maize: `data/mutant_db/maize_ems.parquet`, query column `GeneID`.
- Soybean: 当前只有 `soy_gene_trans.json` 映射，没有突变体数据库；如果用户查询大豆，返回明确的 no database 说明。

## Behavior

- 该 skill 不使用 LLM 生成查询代码，由后端注册 executor 确定性执行。
- 如果用户明确物种，只查对应物种。
- 如果用户没有明确物种，根据基因 ID 形态或映射命中判断物种。
- 如果命中记录，返回 `has_mutant: true`、`total_hits` 和前若干条记录。
- 水稻 BGBIO 命中时返回 `purchase_url`，格式为 `https://www.seedseek.cn/?locus={LOC_ID}`；最终回答应把该链接给用户，并说明可在该页面查看材料并下单购买。
- 拟南芥 ABRC/NASC 命中时返回 `purchase_url`，格式为 `https://abrc.osu.edu/stocks?search%5Btaxon%5D=Arabidopsis+thaliana&search%5Bsearch_text%5D={AT_ID}&search%5Bsearch_fields%5D=All`；最终回答应把该链接给用户，说明可在 ABRC stocks 页面继续查看对应材料。
- 玉米 Maize EMS DB 命中时返回 `purchase_url`，格式为 `https://www.elabcaas.cn/memd/public/index.html#/pages/search/geneid`；这是 MEMD 的 GeneID 搜索入口，最终回答应提示用户在该页面输入对应 GeneID 查看突变体材料并下单。
- 默认最多返回 30 条记录；用户可用 `top N` 或 `limit N` 指定，最大 100。
- 如果映射成功但数据库没有命中，返回 `not_found`，并说明是数据库中没有该基因记录。

## Result Shape

```json
{
  "status": "completed",
  "analysis": "gene_mutant_query",
  "query": "LOC_Os04g54860 有突变体吗？",
  "record_limit": 30,
  "species_searched": ["rice"],
  "genes": ["LOC_Os04g54860"],
  "gene_mappings": [
    {
      "input": "LOC_Os04g54860",
      "species": "rice",
      "species_label": "rice",
      "canonical_id": "LOC_Os04g54860",
      "query_id": "LOC_Os04g54860",
      "matched_by": "gene_trans"
    }
  ],
  "matches": [
    {
      "input": "LOC_Os04g54860",
      "species": "rice",
      "species_label": "rice",
      "database": "BGBIO",
      "canonical_id": "LOC_Os04g54860",
      "query_id": "LOC_Os04g54860",
      "matched_by": "gene_trans",
      "has_mutant": true,
      "total_hits": 7,
      "returned_records": 7,
      "purchase_url": "https://www.seedseek.cn/?locus=LOC_Os04g54860",
      "records": []
    }
  ],
  "not_found": []
}
```
