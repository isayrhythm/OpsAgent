# Skill Template

Skill 文件放在项目根目录的 `skill/` 目录下，文件扩展名必须是 `.md`。后端会在每次请求时扫描 `skill/*.md`，所以模板不要放进 `skill/`，避免被当作可执行能力加载。

## Frontmatter

```markdown
---
name: query_gene_expression
version: 1
description: 当用户请求查询拟南芥基因表达时触发
trigger: 查询拟南芥基因表达量、基因在组织中的表达水平、gene expression 查询
execution_mode: generated_python
data_paths: data/example_gene_expression.csv
---
```

字段说明：

- `name`：唯一 Skill 名称，建议使用小写 snake_case。
- `version`：Skill 文档版本，当前用数字或短字符串即可。
- `description`：简短描述，给路由器判断用途。
- `trigger`：更具体的触发条件，描述什么用户请求应该命中这个 Skill。
- `execution_mode`：执行模式。当前支持约定值 `generated_python`。
- `data_paths`：Skill 可使用的数据路径，多个路径用英文逗号分隔。

## Body Contract

```markdown
# Query Gene Expression Skill

## Contract

- 输入：用户自然语言请求，可能包含 gene_id、tissue 或比较意图。
- 输出：JSON/dict 或 list，必须可 JSON 序列化。
- 执行方式：根据本 Skill 文档生成完整 Python 代码，代码必须把最终结果赋值给 `result`。

## Instructions

- 根据用户的请求，生成 Python 代码查询数据。
- 只输出完整可执行代码，不输出解释。
- 最终结果必须赋值给变量 `result`。
- 代码不用写注释，在完成用户所需信息的范围内尽量简洁。
- 不要包含危险操作，例如 `os.system`、删除文件、启动子进程等。

## Data

- 数据位置：data/example_gene_expression.csv
- 文件结构：说明每一列的列名、数据类型和含义。
- 读取示例：`pd.read_csv("data/example_gene_expression.csv")`
```

## Minimal Skeleton

```markdown
---
name: your_skill_name
version: 1
description: 简短说明这个 Skill 处理什么任务
trigger: 详细说明哪些用户请求应该触发这个 Skill
execution_mode: generated_python
data_paths: data/your_data.csv
---

# Your Skill Name

## Contract

- 输入：用户自然语言请求。
- 输出：JSON/dict 或 list，必须可 JSON 序列化。
- 执行方式：生成完整 Python 代码，最终结果赋值给 `result`。

## Instructions

- 根据用户请求生成 Python 代码。
- 只输出完整可执行代码，不输出解释。
- 最终结果必须赋值给变量 `result`。
- 不要包含危险操作。

## Data

- 数据位置：data/your_data.csv
- 文件结构：

| 列名 | 数据类型 | 描述 |
| ---- | -------- | ---- |
| id   | str      | 示例 |

读取示例：

`pd.read_csv("data/your_data.csv")`
```
