---
name: query_gene_expression
description: 当用户请求查询拟南芥基因表达时触发
---

# Query Gene Expression Skill

## Instructions
- 根据用户的请求，生成 Python 代码去查询我数据库或表格。
- 生成的代码必须输出一个 json/dict，包含用户所想要知道的信息。
- 只需要生成完整的可执行代码，除此之外不需要任何输出。
- 代码不用写注释，在完成用户所需要的信息范围内，力求简洁。
- 不要包含危险操作（os.system, 删除文件等）。



## **数据 **

- 数据位置：data/example_gene_expression.csv
- 文件结构：CSV 文件，三列，以,号隔开，每列含义如下：

| 列名      | 数据类型 | 描述                                                         |
| --------- | -------- | ------------------------------------------------------------ |
| `gene_id` | str      | 基因 ID，例如 `"AT1G00001"`。每个基因在不同组织会重复出现。  |
| `tissue`  | str      | 表示组织类型，例如 `"leaf"`、`"root"`、`"stem"`、`"flower"`。 |
| `expr`    | float    | 对应基因在该组织中的表达量，取值为随机示例数值（0~100）。    |

读取示例：

pd.read_csv("data/example_gene_expression.csv")

数据量

- 4 个基因 × 4 个组织 → 共 16 行
