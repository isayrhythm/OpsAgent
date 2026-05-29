---
name: primer_query
version: 1
description: 查询本地预设计引物数据库，返回某个基因的突变体筛选/鉴定引物、克隆/CDS 扩增引物或 qPCR 引物；适用于用户询问“设计引物”“查询引物”“qPCR 引物”“克隆引物”“突变体筛选引物”“PCR 产物长度”等问题。
trigger: 引物查询、引物设计、primer design、primer query、qPCR 引物、RT-qPCR 引物、克隆引物、CDS 扩增引物、ORF 引物、突变体筛选引物、基因型鉴定引物、T-DNA/EMS 鉴定引物、PCR product length、amplicon length；例如“帮我查 LOC_Os01g66100 的 qPCR 引物”“AGIS_Os01g058220 的突变体筛选引物”“Glyma.01G000100 有哪些引物？”
execution_mode: deterministic_python
executor: primer_query
argument_resolver: message
input_schema: skill/schemas/primer_query.input.json
output_schema: skill/schemas/primer_query.output.json
data_paths: data/primers/clone.parquet, data/primers/mutant.parquet, data/primers/qpcr.parquet, data/gene_trans/ath_gene_trans.json, data/gene_trans/rice_gene_trans.json, data/gene_trans/maize_gene_trans.json, data/gene_trans/soy_gene_trans.json
---

# Primer Query Skill

## Contract

- 输入是用户自然语言请求，可以包含基因 ID、基因别名、物种和引物用途。
- 执行器先用 LLM 将请求解析为 `genes`、`species`、`primer_sources` 和 `top_k`。
- `primer_sources` 只能是：
  - `mutant`: 突变体筛选、基因型鉴定、T-DNA/EMS 鉴定引物。
  - `clone`: 克隆、CDS/ORF/full-length 扩增引物。
  - `qpcr`: qPCR/RT-qPCR 表达检测引物。
  - `auto`: 用户只泛泛说“设计/查询引物”时使用，按 `mutant -> clone -> qpcr` 顺序返回第一个命中的引物类型。
- 查询使用本地预设计引物库，不现场运行 Primer3，也不返回基因全长序列。
- 每条引物返回 forward/reverse 序列、长度、Tm、GC%、自互补指标和 `product_length`。
- `product_length` 是数据库中预先计算好的 PCR 扩增产物长度，不是运行时从 FASTA 重新计算。

## Data

- `data/primers/mutant.parquet`: mutant screening / genotyping primers.
- `data/primers/clone.parquet`: clone / CDS amplification primers.
- `data/primers/qpcr.parquet`: qPCR primers.
- `data/gene_trans/*_gene_trans.json`: maps aliases and non-standard IDs to the query ID used by primer tables.

## Behavior

- 如果用户指定 qPCR、克隆或突变体筛选用途，只查对应表。
- 如果用户没有指定用途，使用 `auto`，只返回第一个命中的引物类型，不把三类全部堆给用户。
- 如果输入 ID 需要映射，例如 `LOC_Os... -> AGIS_Os...`、`GmW82... -> Glyma...`，最终回答必须说明映射关系。
- 如果查询不到，必须说明：该基因可能序列过短、GC含量异常（过高或过低）、存在重复序列、易形成发夹结构或引物二聚体、Tm值不匹配、末端稳定性不足，导致无法设计正确引物。不要编造引物序列。
- 最终回答应优先用表格展示引物对，并包含 `product_length`。

## Result Shape

```json
{
  "status": "completed",
  "analysis": "primer_query",
  "query": "AGIS_Os01g058220 的 qPCR 引物",
  "classification": {
    "genes": ["AGIS_Os01g058220"],
    "species": ["rice"],
    "primer_sources": ["qpcr"],
    "top_k": 10
  },
  "matches": [
    {
      "input": "AGIS_Os01g058220",
      "species": "rice",
      "canonical_id": "AGIS_Os01g058220",
      "query_id": "agis_os01g058220",
      "primer_source": "qpcr",
      "total_hits": 10,
      "returned_primers": 10,
      "primers": [
        {
          "primer_pair": 1,
          "forward_sequence": "GACAGCAGCTCAATCATGCG",
          "forward_tm": 59.972,
          "forward_gc": 55,
          "reverse_sequence": "GATGTTGATGACCATGGCGC",
          "reverse_tm": 59.97,
          "reverse_gc": 55,
          "product_length": 195
        }
      ]
    }
  ],
  "not_found": []
}
```
