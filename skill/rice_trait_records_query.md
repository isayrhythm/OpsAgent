---
name: rice_trait_records_query
version: 1
description: 当用户请求查询水稻种质材料、群体或品系在上海、杭州、海南环境中的表型性状观测值，比较不同环境表现，筛选特定性状高低值材料，或汇总水稻种质表型数据时触发。本 skill 读取本地水稻种质性状 Parquet 表。
trigger: 水稻种质性状查询、水稻表型数据、水稻品系性状、水稻材料表型、种质资源性状、查询某个水稻材料、比较水稻材料、不同环境表型、上海杭州海南水稻数据、抽穗期、株高、茎秆长度、穗长、穗数、叶长、叶宽、叶角、单株产量、粒长、粒宽、蛋白含量、穗包裹、芒长、颖壳颜色、萌发率；例如“查询 N1-10-10B 在三个环境中的株高”“上海环境株高最高的前 10 个材料”“比较 N1-10-10A 和 N1-10-10B 的性状”
execution_mode: generated_python
data_paths: data/rice_trait/rice_trait_records.parquet
---

# Rice Trait Records Query Skill

## Contract

- 输入：用户关于水稻种质材料表型性状的自然语言问题。
- 输出：JSON/dict 或 list，必须可 JSON 序列化。
- 执行方式：根据本 Skill 文档生成完整 Python 代码，代码必须把最终结果赋值给 `result`。
- 数据文件：`DATA_DIR + "/rice_trait/rice_trait_records.parquet"`。

## Dataset

- 数据已由原始 Excel 工作簿预处理为 Parquet，不要重新读取 Excel。
- 共 `55263` 行：`18421` 个水稻材料在 `Shanghai`、`Hangzhou`、`Hainan` 三个环境中的记录。
- `population` 已前向填充，每一行都能关联到对应群体。
- 原始 sheet 中空格、大小写不一致的同义列已归一化。
- Hainan 原表的 `Leaf width (d)` 被视为表头单位误写，已归并为 `leaf_width_cm`。
- `awn_length_cm` 与 `awn_length_mm` 保留为两个字段，不要未经用户要求自行换算。

## Columns

| 字段 | 含义 |
| --- | --- |
| `environment` | 环境：`Shanghai`、`Hangzhou`、`Hainan` |
| `population` | 群体名称 |
| `id` | 水稻材料 ID，例如 `N1-10-10B` |
| `heading_date_d` | 抽穗期，天 |
| `plant_height_cm` | 株高，厘米 |
| `culm_length_cm` | 茎秆长度，厘米 |
| `panicle_length_cm` | 穗长，厘米 |
| `panicle_number` | 穗数 |
| `leaf_length_cm` | 叶长，厘米 |
| `leaf_width_cm` | 叶宽，厘米 |
| `leaf_angle_degree` | 叶角，度 |
| `grain_yield_g` | 籽粒产量，克 |
| `grain_length_mm` | 粒长，毫米 |
| `grain_width_mm` | 粒宽，毫米 |
| `grain_protein_content_pct` | 籽粒蛋白含量，百分比 |
| `panicle_enclosure` | 穗包裹 |
| `awn_length_cm` | 芒长，厘米，可能为区间文本 |
| `awn_length_mm` | 芒长，毫米，可能为区间文本 |
| `hull_color` | 颖壳颜色 |
| `seed_germination_rate_48h_pct` | 48 小时种子萌发率，百分比 |
| `seed_germination_rate_60h_pct` | 60 小时种子萌发率，百分比 |
| `seed_germination_rate_72h_pct` | 72 小时种子萌发率，百分比 |

## Instructions

- 只使用 `import pandas as pd` 和 `pd.read_parquet(DATA_DIR + "/rice_trait/rice_trait_records.parquet")`。
- 不要导入或使用 `os`、`pathlib`、`subprocess`、`shutil`、`socket`，不要覆盖执行环境已经提供的 `DATA_DIR`。
- 只读取回答用户问题需要的列，避免返回整张表。
- 对材料 ID、环境名称和群体名称进行大小写不敏感匹配。
- 用户询问单个材料时，默认返回该材料在所有环境中的相关记录。
- 用户询问多个材料时，返回可比较的紧凑列表，并按材料 ID 和环境排序。
- 用户要求排名或筛选时，默认返回前 `20` 条；用户指定数量时遵循用户数量，但最多返回 `100` 条。
- 数值排序前先使用 `pd.to_numeric(..., errors="coerce")` 并排除空值。
- 输出中使用 `None` 表示缺失值，不要输出 `NaN`。
- 返回结果时包含查询条件、匹配行数、使用的字段、记录列表，以及必要的简短统计摘要。
- 如果无法识别材料 ID、环境或性状，返回错误信息并提示用户补充条件。
- 如果没有匹配记录，返回空记录列表并明确说明未命中，不要编造数据。

## Expected Result Shape

```python
result = {
    "query": "用户请求",
    "filters": {
        "ids": ["N1-10-10B"],
        "environments": ["Shanghai", "Hangzhou", "Hainan"],
        "trait_columns": ["plant_height_cm"]
    },
    "matched_rows": 3,
    "records": [
        {
            "environment": "Shanghai",
            "population": "RIL1 (Kasalath × Huanghuazhan)",
            "id": "N1-10-10B",
            "plant_height_cm": 160.9
        }
    ],
    "summary": {}
}
```
