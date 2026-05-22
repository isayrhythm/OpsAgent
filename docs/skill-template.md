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
- `execution_mode`：执行模式。生成代码型 Skill 使用 `generated_python`；确定性 Skill 使用 `deterministic_*` 约定值，例如 `deterministic_python` 或 `deterministic_python_r`。
- `data_paths`：Skill 可使用的数据路径，多个路径用英文逗号分隔。
- `executor`：确定性 Skill 必填，必须对应后端已注册 executor；生成代码型 Skill 不填。
- `argument_resolver`：确定性 Skill 可填，用于把当前请求和上下文解析成 executor 参数。
- `input_schema`：确定性 Skill 可填，指向输入 JSON schema。
- `output_schema`：确定性 Skill 可填，指向输出 JSON schema。

## Generated Skill Body Contract

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

## Deterministic Skill Skeleton

确定性 Skill 不依赖 LLM 临时生成执行代码。它的 frontmatter 负责把路由命中的 Skill 接到已注册 executor，并用 schema 约束调用参数和结果。

```markdown
---
name: your_deterministic_skill
version: 1
description: 简短说明这个 Skill 处理什么任务
trigger: 详细说明哪些用户请求应该触发这个 Skill
execution_mode: deterministic_python
executor: your_registered_executor
argument_resolver: message
input_schema: skill/schemas/your_deterministic_skill.input.json
output_schema: skill/schemas/your_deterministic_skill.output.json
data_paths: data/your_data.csv
---

# Your Deterministic Skill

## Contract

- 输入：由 `argument_resolver` 解析后的 JSON 参数，结构必须匹配 `input_schema`。
- 输出：executor 返回的 JSON 结果，结构必须匹配 `output_schema`。
- 执行方式：调用后端已注册 executor；不要指望 deterministic Skill 回退到代码生成。

## Instructions

- 写清这个能力处理的数据边界、失败条件和结果含义。
- 如果依赖上传文件，说明需要哪些 intake 产物或 `data_profiles` 条件。
```

示例 schema：

```json
{
  "type": "object",
  "required": ["message"],
  "properties": {
    "message": {
      "type": "string",
      "minLength": 1
    }
  },
  "additionalProperties": false
}
```

## Runtime Boundary

- 上传文件先由 `intake` 修复、识别和生成标准文件；`intake` 不是 Skill。
- Skill 路由使用当前问句、历史和 `data_profiles` 判断要加载哪些 Skill。
- 生成代码型 Skill 走文档指令到代码生成执行。
- 确定性 Skill 走 `resolve arguments -> validate input -> execute -> validate output`，缺少注册 executor 时直接失败。
