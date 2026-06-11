# OpsAgent

OpsAgent 是一个 LangGraph + FastAPI + React + Skill contract 的最小可运行 Agent 骨架。它支持普通对话、动态 Skill 装载、确定性工具调用、Search Skill、多源网络搜索，以及用于复杂问题的 Deep Research DAG 编排。

核心流程：

```text
主 Agent
  -> 路由普通回答
  -> 使用单个或多个 Skill
  -> 进入 Deep Research Graph
       -> 规划任务
       -> 校验计划
       -> 执行 DAG
       -> 评估步骤
       -> 修复或继续
       -> 综合答案
```

## 目录

- `backend/`：FastAPI API、LangGraph 工作流、任务管理和后端服务。
- `backend/app/agents/`：主 Agent、Deep Research graph、任务状态编排。
- `backend/app/llm/`：模型配置与提示词。
- `backend/app/tools/`：工具型能力，例如网络搜索规划与搜索执行。
- `backend/app/skill_tools/`：确定性 Skill executor。当前示例包含 trait 查询、gene info 查询、BLAST、primer 等。
- `backend/app/services/`：任务、路由、Skill runtime、代码执行等后端服务。
- `frontend/`：React + Vite 前端聊天界面。
- `skill/`：Skill markdown 定义。新增 `*.md` 后，后端会动态扫描并纳入路由和 Deep Research 规划。
- `memory/`：短期记忆、上传文件和后续长期记忆的本地目录。
- `data/`：本地示例数据和数据库文件。
- `tests/`：后端测试。

## 功能

- 普通聊天：无工具问题直接回答。
- Skill 路由：根据用户问题选择已注册 Skill。
- 多 Skill 执行：同一轮可以调用多个相关 Skill。
- Search Skill：自动或强制判断是否需要搜索，重写 query，调用 Tavily / Quark，整理 evidence。
- Deep Research：对复杂研究问题生成 plan，按 DAG 执行，并在前端展示每个节点状态和使用的工具。
- 本地数据查询：当前示例支持 trait2gene、gene info 等确定性查询。
- SSE 进度：后端推送思考、工具调用、研究计划、步骤完成和最终答案流。

## 配置

复制环境变量模板：

```powershell
Copy-Item .env.example .env
```

然后编辑项目根目录的 `.env`：

```dotenv
DEEPSEEK_API_KEY=your_deepseek_api_key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_ROUTER_MODEL=deepseek-v4-flash
DEEPSEEK_ANSWER_MODEL=deepseek-v4-pro
DEEPSEEK_CODE_MODEL=deepseek-v4-pro

TAVILY_API_KEY=your_tavily_api_key
TAVILY_BASE_URL=https://api.tavily.com

QUARK_SEARCH_API_KEY=your_quark_search_api_key
QUARK_SEARCH_BASE_URL=your_quark_search_base_url
QUARK_SEARCH_WORKSPACE=default
QUARK_SEARCH_SERVICE_ID=ops-web-search-001
QUARK_SEARCH_QUERY_REWRITE=true
QUARK_SEARCH_CONTENT_TYPE=snippet

OPSAGENT_EXECUTION_TIMEOUT_SECONDS=20
OPSAGENT_MEMORY_DIR=memory
```


## 启动

后端默认端口是 `8001`：

```powershell
python -m pdm use 3.11
python -m pdm install
python -m pdm run api
```

前端默认端口是 `5173`：

```powershell
npm --prefix frontend install
npm --prefix frontend run dev
```

访问：

```text
http://127.0.0.1:5173
```

健康检查：

```text
http://127.0.0.1:8001/api/health
```

## API

- `GET /api/health`：健康检查。
- `GET /api/skills`：实时扫描并返回当前 Skill 列表。
- `POST /api/uploads`：上传文件到短期记忆目录。
- `POST /api/chat`：创建聊天任务，返回 `task_id` 和 SSE 地址。
- `GET /api/tasks/{task_id}/events`：监听任务进度、研究计划、步骤状态和最终结果。

上传文件不会把完整文件内容直接塞进模型上下文。聊天请求只携带附件元信息，例如 `file_id`、`filename`、`content_type`、`size` 和后端保存路径。需要读取文件内容时，应由专门 Skill 根据路径处理。

## Skill Contract

每个 Skill 是一个 `skill/*.md` 文件，文件头部使用 frontmatter 描述能力边界。Router / Deep Research planner 会读取这些元信息，把 Skill 纳入计划编排。

示例：

```markdown
---
name: query_gene_info
version: 1
description: 查询基因的基础信息、表达信息、功能注释和相关数据库记录。
trigger: 用户请求查询基因详情、基因功能、基因注释、候选基因信息。
execution_mode: deterministic_python
executor: query_gene_info
argument_resolver: message
answer_requirements:
  - 说明匹配到的基因 ID 或 symbol。
  - 总结关键功能和证据来源。
---
```

关键字段：

- `name`：Skill 的唯一名称。
- `description`：给 planner 和 router 看的能力说明。
- `trigger`：适合调用该 Skill 的用户意图。
- `execution_mode`：执行模式，例如 `deterministic_python` 或 `generated_python`。
- `executor`：确定性 Skill 需要注册到 runtime 的 executor 名称。
- `answer_requirements`：该 Skill 结果在最终回答中的要求。

新增 Skill 后，如果只是写了 markdown，它会被看见并参与规划；如果要真实执行，还需要在 `backend/app/services/skill_runtime.py` 注册对应 executor。

## Deep Research

Deep Research 适合需要多步证据整合的问题，例如：

```text
深度研究一下水稻耐盐相关基因有哪些？他们有什么功能？
```

典型流程：

```text
classify_intent
  -> plan_research
  -> validate_plan
  -> execute_dag
  -> evaluate_steps
  -> repair_or_continue
  -> synthesize_answer
```

后端会把上游步骤结果传给下游步骤。例如 `trait2gene_query` 得到的候选基因，会进入后续 `query_gene_info` 的输入上下文。

## 测试

运行后端测试：

```powershell
python -m pdm run test
```

构建前端：

```powershell
npm --prefix frontend run build
```


## 当前边界

- 这是中央编排式 Agent，不是无限递归的自由多智能体系统。
- Deep Research 会读取 Skill catalog 并自动规划，但只有注册了 executor 的 Skill 才能稳定真实执行。
- 当前还没有持久化 checkpoint。刷新或进程重启后的任务恢复能力需要后续补充。
- 长记忆目录已经预留，但高价值结论抽取和向量化检索还需要继续设计。
- 生成代码型 Skill 仍需要更强的沙箱隔离，不应执行不可信 Skill。
