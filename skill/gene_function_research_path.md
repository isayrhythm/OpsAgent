---
name: query_gene_function_research_path
version: 1
description: 查询拟南芥基因功能如何被实验逐步建立的研究路径和证据链
trigger: 用户询问某个拟南芥基因的功能研究路径、功能研究路线、功能证据链、功能是怎么研究出来的、研究步骤、实验路径
execution_mode: deterministic_python
data_paths: data/final_atha_top30_integrated_output.csv
---

# Gene Function Research Path Skill

## Contract

- 输入是用户关于拟南芥基因功能研究路径的查询，通常包含基因 ID，例如 `HY2`、`PHYA`、`ABI1`。
- 固定数据源是 `data/final_atha_top30_integrated_output.csv`。
- 查询索引是 CSV 的 `targetGene` 基因字段；`title` 只作为文献信息。
- `final_md_content` 中 `Route of Gene Function Exploration` 的 step 表是功能研究路径主数据。
- 输出必须保留结构化 `matches` 和 `ui_blocks`，供前端在消息流中绘制研究路径，不返回独立 HTML 报告。

## Result Shape

```json
{
  "status": "completed",
  "analysis": "gene_function_research_path",
  "genes": ["HY2"],
  "matches": [
    {
      "paper_id": "Atha_0",
      "title": "paper title",
      "gene_id": "HY2",
      "steps": [
        {
          "step": "1",
          "stage_operation": "stage title",
          "figures": "Fig. 2",
          "hypothesis": "hypothesis",
          "methods": "methods",
          "results": "results",
          "step_conclusion": "step conclusion"
        }
      ]
    }
  ],
  "ui_blocks": [
    {
      "type": "gene_function_research_path",
      "id": "stable block id",
      "gene_id": "HY2",
      "title": "paper title",
      "steps": []
    }
  ]
}
```
