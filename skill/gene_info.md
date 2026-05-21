---
name: query_gene_info
version: 1
description: 当用户请求查询水稻、玉米或大豆基因的基本信息、表达信息、表达研究、注释、功能、位置、别名对应关系或关联性状文本块时触发
trigger: 查询基因信息、基因表达信息、基因表达研究、表达信息、表达研究、基因注释、基因功能、基因位置、基因长度、GO注释、KEGG注释、结构域、转录本、关联性状、文献信息、gene info、gene expression info、gene annotation、rice gene、水稻基因、maize gene、玉米基因、soybean gene、大豆基因、ID转换、基因别名转换
execution_mode: generated_python
data_paths: data/gene_info/rice_gene_info.json, data/gene_info/rice_gene_trans.json, data/gene_info/maize_gene_info.json, data/gene_info/maize_gene_trans.json, data/gene_info/soy_gene_info.json, data/gene_info/soy_gene_trans.json
---

# Query Gene Info Skill

## Contract

- 输入：用户自然语言请求，通常包含一个或多个基因 ID、旧 ID、别名、gene symbol、转录本 ID，可能包含物种信息。
- 输出：JSON/dict 或 list，必须可 JSON 序列化。
- 执行方式：根据本 Skill 文档生成完整 Python 代码，代码必须把最终结果赋值给 `result`。

## Instructions

- 根据用户请求生成 Python 代码查询本地 JSON 数据。
- 只输出完整可执行代码，除此之外不需要任何输出。
- 最终结果必须赋值给变量 `result`。
- 代码不用写注释，在完成用户所需信息的范围内力求简洁。
- 不要包含危险操作，例如 `os.system`、删除文件、启动子进程等。
- 不要导入或使用 `os`、`pathlib`、`subprocess`、`shutil`、`socket`。
- 不要直接调用内置 `open()`。读取 JSON 时使用 `import json, io`，再使用 `io.open(path, "r", encoding="utf-8")`。
- 数据目录变量可直接使用执行环境提供的 `DATA_DIR`，例如：`DATA_DIR + "/gene_info/rice_gene_trans.json"`。
- 如果用户明确说明物种，只读取对应物种的 `*_gene_trans.json` 和必要的 `*_gene_info.json`。
- 如果用户没有明确说明物种，先在三个物种的 `*_gene_trans.json` 和标准 ID 中查找；只对命中的物种读取对应的 `*_gene_info.json`。
- 不要为了模糊搜索而全量扫描 `*_gene_info.json` 的文本内容；这些文件很大。只有在已确定标准基因 ID 后才读取对应 info 文件并取值。
- 对用户输入中的基因 ID 或别名做大小写兼容匹配：`*_gene_trans.json` 的 key 通常是小写，查询别名时先使用 `term.lower()`。
- 对标准基因 ID 先尝试原样匹配 `*_gene_info.json` 的 key，再尝试用小写映射回原始 key。
- 如果用户请求多个基因，返回每个基因的匹配结果。
- 如果找不到匹配，返回 `matched: false`，并说明尝试过的物种和查询词。
- 如果用户没有提供可识别的基因 ID、别名或 symbol，返回错误信息，提示用户补充基因 ID 或物种。

## Species

| 物种 | 用户可能说法 | 标准 ID 示例 | trans 文件 | info 文件 |
| ---- | ------------ | ------------ | ---------- | --------- |
| rice | 水稻、rice、Oryza、Os、LOC_Os、RAP | `AGIS_Os09g012290` | `data/gene_info/rice_gene_trans.json` | `data/gene_info/rice_gene_info.json` |
| maize | 玉米、maize、corn、Zea、Zm、B73 | `Zm00001eb000020` | `data/gene_info/maize_gene_trans.json` | `data/gene_info/maize_gene_info.json` |
| soy | 大豆、soy、soybean、Glycine、Glyma、GmW82 | `Glyma.15G027500` | `data/gene_info/soy_gene_trans.json` | `data/gene_info/soy_gene_info.json` |

## Data

### `*_gene_trans.json`

- JSON 对象。
- key：基因别名、旧版本 ID、外部数据库 ID、gene symbol 或转录本相关 ID，通常为小写。
- value：该物种 `*_gene_info.json` 中使用的标准基因 ID。
- 示例：
  - `rice_gene_trans.json`: `"loc_os09g03110" -> "AGIS_Os09g012290"`
  - `maize_gene_trans.json`: `"zm00001d027231" -> "Zm00001eb000020"`
  - `soy_gene_trans.json`: `"gmw82.15g028400" -> "Glyma.15G027500"`

### `*_gene_info.json`

- JSON 对象。
- key：标准基因 ID。
- value：该基因对应的 markdown 文本块，包含基本信息、位置、长度、转录本、GO/KEGG/结构域、功能、表达研究、关联性状或文献信息等。
- 返回时保留原始文本块，不要自行丢弃关键信息。

## Expected Result Shape

结果建议使用以下结构：

```python
result = {
    "query_terms": ["用户请求中识别出的基因词"],
    "species_searched": ["rice", "maize", "soy"],
    "matches": [
        {
            "input": "原始查询词",
            "species": "rice",
            "matched": True,
            "matched_by": "canonical_id 或 alias",
            "canonical_id": "AGIS_Os09g012290",
            "text": "info 文件中的完整文本块"
        }
    ],
    "not_found": []
}
```

## Implementation Hints

- 可以用 `re.findall()` 从用户请求中提取候选词。候选词应覆盖包含字母、数字、点、下划线、连字符的 token，例如 `LOC_Os09g03110`、`Zm00001eb000020`、`zm00001d027231`、`Glyma.15G027500`、`GmW82.15G028400`。
- 过滤掉明显不是基因 ID 的普通词，例如 `gene`、`info`、`query`、`rice`、`maize`、`soybean`。
- 对每个候选词，每个物种最多返回一个标准 ID 命中结果。
- 如果用户问“这个 ID 对应哪个标准 ID”，即使没有要求完整信息，也应返回 `canonical_id`，可以同时返回 `text`。
