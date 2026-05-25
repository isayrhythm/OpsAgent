# OpsAgent 接口层契约

本文档记录当前后端实际提供的接口、前端依赖格式、SSE 执行轨迹、专业模块 Skill 契约、错误码与推荐的认证/并发策略。

## 1. 当前接口总览

| 方法 | 路由 | 用途 | 当前返回方式 |
|---|---|---|---|
| `GET` | `/api/health` | 健康检查 | 一次性 JSON |
| `GET` | `/api/skills` | 获取当前已加载 Skill 列表 | 一次性 JSON |
| `POST` | `/api/chat` | 创建一次智能体任务 | 一次性 JSON，随后通过 SSE 取执行过程 |
| `GET` | `/api/tasks/{task_id}/events` | 订阅 chat 任务事件 | SSE |
| `POST` | `/api/tasks/{task_id}/cancel` | 取消运行中的任务 | 一次性 JSON |
| `POST` | `/api/uploads` | 上传文件并启动 intake | 一次性 JSON，随后通过 SSE 取 intake 过程 |
| `GET` | `/api/uploads/{task_id}/events` | 订阅 upload intake 事件 | SSE |
| `GET` | `/api/artifacts/{run_id}/{filename:path}` | 下载分析结果文件 | 文件响应 |

## 2. 前端依赖的 HTTP 接口

### 2.1 健康检查

`GET /api/health`

响应：

```json
{
  "status": "ok"
}
```

### 2.2 Skill 列表

`GET /api/skills`

响应：

```json
[
  {
    "name": "gene_phenotype_prediction",
    "description": "根据基因 ID 查询预测性状关联",
    "version": "1",
    "trigger": "用户查询基因可能关联性状",
    "execution_mode": "deterministic_query",
    "data_paths": ["data/GenePredictor/rice_lte_result.parquet"],
    "path": "E:/workspace/OpsAgent/skill/gene_phenotype_prediction.md"
  }
]
```

### 2.3 创建对话任务

`POST /api/chat`

请求：

```json
{
  "message": "COLD1 是什么基因？",
  "user_id": "default",
  "session_id": "conversation-id",
  "history": [
    {"role": "user", "content": "上一个问题"},
    {"role": "assistant", "content": "上一个回答"}
  ],
  "attachments": [
    {
      "file_id": "file-id",
      "filename": "matrix.csv",
      "content_type": "text/csv",
      "size": 12345,
      "path": "E:/workspace/OpsAgent/memory/...",
      "intake": {}
    }
  ],
  "detached_files": [
    {"file_id": "file-id", "filename": "matrix.csv"}
  ],
  "web_search": false
}
```

字段说明：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `message` | string | 是 | 当前用户输入，不能为空 |
| `user_id` | string | 否 | 当前默认 `default`；生产环境不应信任前端传入 |
| `session_id` | string/null | 否 | 前端会话 ID，用于读写本地 memory |
| `history` | array | 否 | 前端传入的历史消息；为空时后端会尝试从 memory 读取最近历史 |
| `attachments` | array | 否 | 当前会话仍挂载的上传文件 |
| `detached_files` | array | 否 | 用户手动移除的文件列表，用于减少上下文误判 |
| `web_search` | boolean | 否 | 是否启用网络搜索 |

响应：

```json
{
  "task_id": "task-id",
  "events_url": "/api/tasks/task-id/events"
}
```

### 2.4 取消任务

`POST /api/tasks/{task_id}/cancel`

响应：

```json
{
  "task_id": "task-id",
  "cancelled": true
}
```

说明：

- `cancelled=true` 表示后端已请求取消 asyncio task。
- 如果任务已完成、已失败或不存在可取消 runner，则返回 `cancelled=false`。
- 当前取消不会回滚已产生的文件或中间结果。

### 2.5 上传文件

`POST /api/uploads`

请求类型：`multipart/form-data`

字段：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `user_id` | string | 否 | 默认 `default` |
| `session_id` | string | 是 | 文件保存到当前会话目录 |
| `files` | file[] | 是 | 一个或多个上传文件 |

响应：

```json
{
  "task_id": "upload-task-id",
  "events_url": "/api/uploads/upload-task-id/events",
  "files": [
    {
      "file_id": "file-id",
      "filename": "matrix.csv",
      "content_type": "text/csv",
      "size": 12345,
      "path": "E:/workspace/OpsAgent/memory/short_term/default/uploads/session-id/...",
      "intake": null
    }
  ]
}
```

### 2.6 下载分析产物

`GET /api/artifacts/{run_id}/{filename:path}`

响应：文件内容。

当前用途：

- 差异蛋白分析报告 HTML
- 转录组差异分析报告 HTML
- 结果表格或静态资源

## 3. SSE 事件契约

### 3.1 Chat 任务事件

`GET /api/tasks/{task_id}/events`

支持：

- `Last-Event-ID`：前端刷新或重连后可从上次事件 ID 继续读取。
- 心跳：后端每约 15 秒可能发送 `ping`。

事件格式：

```text
id: 1
event: progress
data: {"type":"progress","step":1,"status":"正在路由","data":{}}
```

统一 data 结构：

```json
{
  "type": "progress",
  "step": 1,
  "status": "当前状态文本",
  "data": {}
}
```

事件类型：

| event/type | 用途 |
|---|---|
| `progress` | 阶段状态，例如路由、解析参数、调用工具 |
| `thinking_delta` | LLM 生成代码或思考过程的增量 |
| `answer_delta` | 最终自然语言回答的增量 |
| `ui_delta` | 前端可视化组件数据，例如功能研究路径、预测动画后续可扩展 |
| `source_delta` | 网络搜索来源 |
| `result` | 任务完成后的最终结果 |
| `cancelled` | 用户取消任务 |
| `error` | 任务失败 |
| `ping` | SSE 保活事件 |
| `end` | SSE 流结束 |

`result` 示例：

```json
{
  "type": "result",
  "step": 7,
  "status": "完成",
  "data": {
    "skill": "gene_info",
    "skills": ["gene_info"],
    "skill_output": {},
    "skill_outputs": [],
    "answer": "COLD1 是...",
    "web_sources": [],
    "mode": "skill"
  }
}
```

### 3.2 Upload intake 事件

`GET /api/uploads/{task_id}/events`

事件类型：

| event/type | 用途 |
|---|---|
| `progress` | 文件已保存、正在 intake、单文件 intake 完成 |
| `result` | 全部文件 intake 完成 |
| `error` | intake 失败 |
| `ping` | SSE 保活 |
| `end` | SSE 流结束 |

`result` 示例：

```json
{
  "type": "result",
  "step": 4,
  "status": "上传文件 intake 完成",
  "data": {
    "files": [
      {
        "file_id": "file-id",
        "filename": "matrix.csv",
        "content_type": "text/csv",
        "size": 12345,
        "path": "E:/workspace/OpsAgent/memory/...",
        "intake": {
          "status": "ready",
          "data_type": "expression_matrix",
          "data_family": "proteomics",
          "analysis_ready": true,
          "confidence": "high"
        }
      }
    ]
  }
}
```

## 4. 专业模块与工具契约

### 4.1 当前已注册 executor

| Skill | executor | 模式 | 当前输入来源 | 当前超时 |
|---|---|---|---|---|
| `gene_function_research_path` | `gene_function_research_path_query` | deterministic query | `message` | 未单独限制，随任务运行 |
| `gene_phenotype_prediction` | `gene_phenotype_prediction` | deterministic query | `message` | 未单独限制，随任务运行 |
| `differential_protein_analysis` | `differential_protein_analysis` | deterministic analysis | LLM 解析参数 + attachments/intake | 未单独限制，R 进程内部应补 |
| `differential_transcriptomics_analysis` | `differential_transcriptomics_analysis` | deterministic analysis | LLM 解析参数 + attachments/intake | 未单独限制，R 进程内部应补 |

### 4.2 当前 generated skill

| Skill | 模式 | 说明 | 当前超时 |
|---|---|---|---|
| `gene_info` | generated Python | LLM 根据 skill 文档生成 Python 查询本地 gene info JSON | `OPSAGENT_EXECUTION_TIMEOUT_SECONDS`，默认 20 秒 |

### 4.3 现有 schema 文件

| 文件 | 说明 |
|---|---|
| `skill/schemas/differential_protein_analysis.input.json` | 蛋白组差异分析输入 |
| `skill/schemas/differential_protein_analysis.output.json` | 当前较宽松，建议收紧 |
| `skill/schemas/differential_transcriptomics_analysis.input.json` | 转录组差异分析输入 |
| `skill/schemas/differential_transcriptomics_analysis.output.json` | 当前较宽松，建议收紧 |
| `skill/schemas/gene_function_research_path.input.json` | 基因功能研究路径输入 |
| `skill/schemas/gene_function_research_path.output.json` | 当前较宽松，建议收紧 |
| `skill/schemas/gene_phenotype_prediction.input.json` | 基因性状预测输入 |
| `skill/schemas/gene_phenotype_prediction.output.json` | 当前较宽松，建议收紧 |

### 4.4 差异蛋白分析输入

```json
{
  "comparisons": [
    {"numerator": "MT", "denominator": "WT"}
  ],
  "pvalue_cutoff": 0.05,
  "fold_change_cutoff": 1.5,
  "reason": "用户要求 MT vs WT"
}
```

字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `comparisons` | array | 多组比较，组名必须来自 intake 识别出的 sample groups |
| `pvalue_cutoff` | number/null | p 值阈值；为空使用执行器默认值 |
| `fold_change_cutoff` | number/null | Fold Change 阈值；为空使用执行器默认值 |
| `reason` | string | 参数解析原因，仅用于审计和调试 |

### 4.5 转录组差异分析输入

```json
{
  "comparisons": [
    {"numerator": "MT", "denominator": "WT"}
  ],
  "padj_cutoff": 0.05,
  "log2_fc_cutoff": 1.0,
  "reason": "用户要求 MT vs WT"
}
```

字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `comparisons` | array | 多组比较，组名必须来自 intake 识别出的 sample groups |
| `padj_cutoff` | number/null | adjusted p-value 阈值；为空使用执行器默认值 |
| `log2_fc_cutoff` | number/null | log2 Fold Change 阈值；为空使用执行器默认值 |
| `reason` | string | 参数解析原因，仅用于审计和调试 |

### 4.6 基因功能研究路径输入

```json
{
  "message": "HY2 功能研究路径"
}
```

输出建议：

```json
{
  "query": "HY2",
  "matched_gene_id": "AT...",
  "records": [
    {
      "gene_id": "AT...",
      "title": "paper title",
      "steps": [
        {
          "title": "Step 1",
          "hypothesis": "...",
          "evidence": "...",
          "conclusion": "..."
        }
      ]
    }
  ],
  "ui_blocks": [
    {
      "type": "gene_function_research_path",
      "title": "HY2 功能研究路径",
      "items": []
    }
  ]
}
```

### 4.7 基因性状预测输入

```json
{
  "message": "LOC_Os07g48050 可能跟哪些性状相关？"
}
```

输出建议：

```json
{
  "query": "LOC_Os07g48050",
  "mapped_gene_id": "AGIS_Os07g043560",
  "species": "rice",
  "predictions": [
    {
      "phenotype": "grain_number_per_panicle",
      "pred_score": 0.008939
    }
  ],
  "gene_mappings": [
    {
      "input": "LOC_Os07g48050",
      "mapped_to": "AGIS_Os07g043560",
      "source": "rice_gene_trans"
    }
  ]
}
```

### 4.8 突变体查询

已实现 skill：`gene_mutant_query`

触发：

- 用户问某个基因是否有突变体
- 用户问 EMS、突变材料、编辑材料、突变位点、突变样品

输入：

```json
{
  "message": "LOC_Os04g54860 有突变体吗？"
}
```

输出：

```json
{
  "query": "LOC_Os04g54860",
  "normalized_gene_id": "LOC_Os04g54860",
  "species": "rice",
  "has_mutant": true,
  "total_hits": 7,
  "databases": ["bgbio"],
  "records": [
    {
      "database": "bgbio",
      "germplasm_type": "突变体种子",
      "species_or_variety": "水稻/ZH11",
      "vector": "BGK03",
      "gene_id": "LOC_Os04g54860",
      "target_sequence": "GCAGTGGATGCAGGCTGATACGG",
      "validation": "纯合突变，G缺失"
    }
  ],
  "gene_mappings": [
    {
      "input": "COLD1",
      "mapped_to": "LOC_Os04g51180",
      "source": "rice_mutant_gene_trans"
    }
  ]
}
```

推荐数据源：

| 物种 | 数据 | 查询字段 |
|---|---|---|
| 拟南芥 | `data/mutant_db/ath_abrc.parquet` | `gene_id` |
| 水稻 | `data/mutant_db/rice_bgbio.parquet` | `基因号` |
| 玉米 | `data/mutant_db/maize_ems.parquet` | `GeneID` |

推荐映射：

| 文件 | 用途 |
|---|---|
| `data/mutant_db/ath_gene_trans.json` | 拟南芥别名到 ABRC 可查 gene ID |
| `data/mutant_db/rice_gene_trans.json` | 水稻别名、AGIS、RapDB、LOC 到 BGBIO 可查 LOC ID |
| `data/mutant_db/maize_gene_trans.json` | 玉米别名到 Maize EMS 可查 gene ID |
| `data/mutant_db/soy_gene_trans.json` | 大豆别名映射；当前没有对应突变体数据库，仅用于解释 no database |

## 5. 错误码

### 5.1 当前状态

当前没有统一业务错误码。

现有错误主要来自：

- FastAPI/Pydantic：`422`，请求体格式错误。
- FastAPI：`404`，任务或 artifact 不存在。
- FastAPI：`400`，artifact 参数非法。
- SSE `error`：任务执行异常文本。
- SSE `cancelled`：用户取消。

### 5.2 推荐统一错误结构

HTTP 错误和 SSE `error` 的 `data` 都建议统一为：

```json
{
  "code": "SKILL_TIMEOUT",
  "message": "差异分析执行超时",
  "detail": {},
  "retryable": true
}
```

推荐错误码：

| code | HTTP | retryable | 说明 |
|---|---:|---|---|
| `BAD_REQUEST` | 400 | false | 请求参数语义错误 |
| `VALIDATION_ERROR` | 422 | false | 请求 schema 校验失败 |
| `TASK_NOT_FOUND` | 404 | false | 任务不存在 |
| `ARTIFACT_NOT_FOUND` | 404 | false | 文件产物不存在 |
| `TASK_CANCELLED` | 200/SSE | true | 用户取消 |
| `LLM_UNAVAILABLE` | 503 | true | LLM API key 缺失或模型不可用 |
| `LLM_TIMEOUT` | 504 | true | LLM 超时 |
| `ROUTER_FAILED` | 500 | true | skill 路由失败 |
| `SKILL_NOT_FOUND` | 404 | false | 未找到 skill |
| `SKILL_CONTRACT_ERROR` | 500 | false | skill 输入/输出不符合 schema |
| `SKILL_TIMEOUT` | 504 | true | skill 执行超时 |
| `SKILL_EXECUTION_ERROR` | 500 | true | skill 执行失败 |
| `DATA_NOT_READY` | 409 | false | 上传文件 intake 未完成或不适合该分析 |
| `DATASET_NOT_FOUND` | 500 | false | 后端配置的数据文件不存在 |
| `WEB_SEARCH_UNAVAILABLE` | 503 | true | Tavily 或网络搜索不可用 |
| `AUTH_REQUIRED` | 401 | false | 生产环境未登录 |
| `FORBIDDEN` | 403 | false | 无权访问资源 |
| `RATE_LIMITED` | 429 | true | 超过并发或 RPM 限制 |

## 6. 超时策略

### 6.1 当前实际超时

| 模块 | 当前超时 |
|---|---|
| generated Python skill 执行 | `OPSAGENT_EXECUTION_TIMEOUT_SECONDS`，默认 20 秒 |
| DeepSeek 普通 chat | 60 秒 |
| DeepSeek stream chat | 当前无总超时 |
| Tavily web search | 30 秒 |
| SSE wait/ping | 15 秒 |
| deterministic query | 当前无单独超时 |
| R 差异分析 | 当前应补单独超时 |
| upload intake | 当前无单独超时 |

### 6.2 推荐超时

| 模块 | 推荐超时 |
|---|---:|
| 路由 LLM | 10 秒 |
| 参数解析 LLM | 15 秒 |
| 回答生成 LLM | 60 秒 |
| generated Python 执行 | 20 秒 |
| 本地确定性查询 | 10-30 秒 |
| 网络搜索 | 30 秒 |
| upload intake 单文件 | 60-180 秒 |
| 蛋白组差异分析 R | 120-300 秒 |
| 转录组差异分析 R | 300-600 秒 |
| artifact 下载 | 60 秒 |

## 7. 执行轨迹策略

当前策略：

- chat 任务必须通过 SSE 返回轨迹。
- upload intake 必须通过 SSE 返回轨迹。
- artifact 下载不走 SSE。

推荐保持：

- 所有可能超过 1 秒的任务都走 SSE。
- 快速接口如 `/api/health`、`/api/skills` 一次性返回。
- skill 内部阶段建议统一发送 `progress`：
  - `routing`
  - `argument_resolving`
  - `skill_running`
  - `result_evaluating`
  - `answering`
  - `completed`

## 8. 用户认证方案

### 8.1 当前状态

当前没有认证。

实际行为：

- 前端传 `user_id`。
- 后端默认 `user_id=default`。
- 文件和对话保存在 `memory/short_term/{user_id}`。
- CORS 当前为 `allow_origins=["*"]`。

### 8.2 推荐方案

本地开发：

- 可以继续无认证。
- 默认 `user_id=default`。

正式部署：

- 使用 JWT Bearer token。
- 后端验证 JWT 后，从 token 的 `sub` 或 `user_id` claim 得到真实用户 ID。
- 不再信任前端请求体里的 `user_id`。
- `session_id` 仍由前端生成，但后端必须校验该 session 是否属于当前用户。
- CORS 改为明确域名白名单。

推荐请求头：

```http
Authorization: Bearer <jwt>
```

后端内部用户上下文：

```json
{
  "user_id": "jwt.sub",
  "session_id": "frontend-session-id"
}
```

## 9. 并发与限流

### 9.1 当前状态

当前没有显式并发限制。

实际行为：

- 每次 `/api/chat` 创建一个 asyncio task。
- 每次 `/api/uploads` 创建一个 intake asyncio task。
- 多个任务可同时运行。
- 没有全局 semaphore。
- 没有 per-user/per-session 限流。
- 没有 RPM 限制。

### 9.2 推荐并发限制

| 资源 | 推荐限制 |
|---|---:|
| 全局 chat 任务 | 8 |
| 单用户 chat 任务 | 2 |
| 单 session 运行中任务 | 1 |
| LLM router 并发 | 5 |
| LLM answer/code 并发 | 3 |
| R 分析并发 | 1-2 |
| upload intake 并发 | 2 |
| web search 并发 | 3 |

推荐行为：

- 同一个 session 如果已有任务运行，新的消息应返回 `409 TASK_ALREADY_RUNNING`，或自动取消旧任务后启动新任务。
- R 分析必须进入队列，避免多个 R 进程同时吃满机器。
- 文件 intake 可以并发，但每用户限制 1-2 个。
- LLM 请求应加全局 semaphore，避免 API 侧限流。

## 10. 当前缺口清单

| 缺口 | 优先级 | 说明 |
|---|---|---|
| 统一错误码 | 高 | 前端现在只能显示文本错误，不利于重试和用户提示 |
| 每个 skill 严格 output schema | 高 | 目前部分 output schema 太宽松 |
| R 分析单独超时 | 高 | 防止 R 卡死导致任务长期挂起 |
| per-session 单任务限制 | 高 | 防止同一会话并发写 history 或互相覆盖状态 |
| JWT 认证 | 中 | 本地开发不急，部署必须做 |
| 并发 semaphore | 中 | 用户量增加后必须做 |
| generated skill 审计日志 | 中 | 方便复现 LLM 生成代码的问题 |
| artifact 权限校验 | 中 | 生产环境必须确保用户只能访问自己的结果 |

## 11. 推荐落地顺序

1. 补统一错误码和 SSE error `data` 结构。
2. 给 R 分析和 deterministic executor 加 `asyncio.wait_for` 超时。
3. 加 per-session 单任务限制和取消旧任务策略。
4. 收紧所有 output schema。
5. 继续补充 `gene_mutant_query` 的更严格 output schema 和前端展示。
6. 加 JWT 认证和 artifact 权限校验。
7. 加全局并发 semaphore 与 per-user 限流。
