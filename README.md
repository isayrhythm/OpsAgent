# OpsAgent

![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue) ![LangGraph](https://img.shields.io/badge/LangGraph-brightgreen) ![FastAPI](https://img.shields.io/badge/FastAPI-brightgreen) ![React](https://img.shields.io/badge/React-19-blue) ![Vite](https://img.shields.io/badge/Vite-purple) ![License](https://img.shields.io/badge/License-MIT-yellow)

OpsAgent 是一个基于 LangGraph + FastAPI + React + Skill contract 的可扩展 Agent 工作台。它面向任意领域的 RAG、工具调用和多步研究编排：通过 `skill/*.md` 契约、本地数据、文件处理器、搜索工具和确定性 executor 扩展。

当前 RAG 能力主要由三类上下文组成：会话历史、上传文件上下文和外部/本地工具证据。向量索引、长期记忆抽取和领域专用检索可以作为后续 Skill 或服务接入。

核心流程：

```text
用户请求
  -> File Inspector / Web Search Planner
  -> 加载 Skill catalog 和内置工具
  -> Router 判断普通回答、Skill、Shell Command 或 Deep Research
  -> 执行确定性工具、生成代码型 Skill、受限命令或 Deep Research DAG
  -> 汇总工具证据、搜索来源、文件上下文和会话历史
  -> SSE 流式返回进度、来源、UI blocks 和最终答案
```

## 架构

![OpsAgent architecture](docs/ChatGPT%20Image.png)

```text
frontend/
  React + Vite 单页应用
  - 本地会话、附件、SSE 任务流、引用来源、研究步骤 UI

backend/app/main.py
  FastAPI API
  - 聊天任务、上传任务、SSE、取消任务、artifact 下载

backend/app/agents/
  LangGraph 编排层
  - 主 Agent graph
  - Deep Research graph
  - 路由、执行、最终回答、任务状态和工具轨迹

backend/app/tools/
  内置工具层
  - Web Search Planner / Tavily / Quark
  - File Inspector / File Transformer
  - 受限 Shell Command
  - 通用 Tool Runner

backend/app/services/
  后端服务层
  - Skill loader / Skill runtime
  - 生成代码型 Skill executor
  - 结果评估、上下文整理、ID 映射、上传 intake

skill/
  Skill markdown 契约和 JSON schema

data/
  本地数据、索引或领域资源

memory/
  会话历史、上传文件、文件 intake 结果和临时运行目录
```

主 Agent 的 LangGraph 流程：

```text
intake_uploads
  -> load_skills
  -> route
      -> final_answer
      -> execute_skill -> final_answer
      -> execute_command -> final_answer
      -> research_graph
```

Deep Research 子图：

```text
classify_intent
  -> plan_research
  -> validate_plan
  -> execute_dag
  -> evaluate_steps
  -> repair_or_continue
  -> synthesize_answer
```

## 功能

- 普通聊天：无工具问题直接由模型回答。
- Skill 路由：根据用户问题、历史、上传文件 profile 和 Skill catalog 选择能力。
- 多 Skill 执行：同一轮可以调用多个相关 Skill，并把结果统一交给最终回答器。
- Deep Research：对复杂问题生成 DAG，调用搜索、Skill 或受限命令，评估步骤后综合答案。
- Web Search：支持 `off` / `auto` / `force` 模式，可调用 Tavily、Quark 或多源搜索，并输出引用来源。
- 文件上下文：上传后异步生成文件 profile，支持表格、PDF 和 FASTA 等文件形态；具体 Skill 执行前可做二次转换。
- 受限 Shell Command：只允许白名单内的只读命令，限制 shell 语法、路径越界、敏感文件访问和危险操作。
- Artifact 输出：确定性分析可以生成可下载产物，并通过 API 暴露。
- SSE 进度：后端推送思考、工具调用、文件读取、研究计划、来源、UI blocks 和最终结果。
- Tool trace：任务完成后保存本轮工具轨迹，便于前端展示和后续上下文引用。

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
WEB_SEARCH_PROVIDER=tavily

QUARK_SEARCH_API_KEY=your_quark_search_api_key
QUARK_SEARCH_BASE_URL=your_quark_search_base_url
QUARK_SEARCH_WORKSPACE=default
QUARK_SEARCH_SERVICE_ID=ops-web-search-001
QUARK_SEARCH_QUERY_REWRITE=true
QUARK_SEARCH_CONTENT_TYPE=snippet

OPSAGENT_EXECUTION_TIMEOUT_SECONDS=20
OPSAGENT_LLM_STREAM_TIMEOUT_SECONDS=20
OPSAGENT_TASK_RETENTION_SECONDS=120
OPSAGENT_UPLOAD_INTAKE_RETENTION_SECONDS=120
OPSAGENT_CORS_ORIGINS=http://127.0.0.1:5173,http://localhost:5173
OPSAGENT_MEMORY_DIR=memory

OPSAGENT_COMMAND_TOOL_ENABLED=true
OPSAGENT_COMMAND_TOOL_BACKEND=auto
OPSAGENT_COMMAND_TOOL_TIMEOUT_SECONDS=20
OPSAGENT_COMMAND_TOOL_MAX_OUTPUT_CHARS=12000
OPSAGENT_COMMAND_TOOL_WORKDIR=memory/command_tool
OPSAGENT_COMMAND_TOOL_CWD=.
```

`OPSAGENT_COMMAND_TOOL_BACKEND` 可选 `auto`、`wsl`、`docker`、`native`。开放给不可信用户时，建议关闭命令工具，或使用 Docker 后端并只挂载临时工作目录。

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
- `POST /api/uploads`：上传文件到短期记忆目录，并创建文件 intake 任务。
- `GET /api/uploads/{task_id}/events`：监听上传文件的 File Inspector 进度。
- `POST /api/chat`：创建聊天任务，返回 `task_id` 和 SSE 地址。
- `GET /api/tasks/{task_id}/events`：监听聊天任务进度、研究计划、步骤状态和最终结果。
- `POST /api/tasks/{task_id}/cancel`：取消仍在运行的聊天任务。
- `GET /api/artifacts/{run_id}/{filename}`：下载工具或 Skill 生成的 artifact。

上传文件不会把完整文件内容直接塞进模型上下文。上传后先由 File Inspector 生成通用文件 profile；聊天请求携带附件元信息和 profile。只有当具体 Skill 被选中时，File Transformer 才会根据该 Skill 的 contract 把文件转换为执行器需要的结构。

## Skill Contract

每个 Skill 是一个 `skill/*.md` 文件，文件头部使用 frontmatter 描述能力边界。Router / Deep Research planner 会读取这些元信息，把 Skill 纳入计划编排。

示例：

```markdown
---
name: query_local_knowledge
version: 1
description: 查询某个本地知识库或业务数据源，并返回结构化证据。
trigger: 用户请求查询该知识库覆盖范围内的实体、记录、证据或统计结果。
execution_mode: deterministic_python
executor: query_local_knowledge
argument_resolver: message
input_schema: skill/schemas/query_local_knowledge.input.json
output_schema: skill/schemas/query_local_knowledge.output.json
answer_requirements: 说明匹配条件; 总结关键证据; 不要编造数据源不存在的结论
---
```

关键字段：

- `name`：Skill 的唯一名称。
- `description`：给 planner 和 router 看的能力说明。
- `trigger`：适合调用该 Skill 的用户意图。
- `execution_mode`：执行模式，例如 `deterministic_python`、`generated_python` 或内置工具模式。
- `executor`：确定性 Skill 需要注册到 runtime 的 executor 名称。
- `argument_resolver`：参数解析策略，例如直接使用消息或解析文件分析参数。
- `input_schema` / `output_schema`：JSON schema contract，用于执行前后校验。
- `answer_requirements`：该 Skill 结果在最终回答中的要求。

新增 Skill 后，如果只是写了 markdown，它会被看见并参与路由和 Deep Research 规划；如果要稳定真实执行，还需要在 `backend/app/services/skill_runtime.py` 注册对应 executor，或实现受控的生成代码执行路径。

## Deep Research

Deep Research 适合需要多步证据整合的问题，例如：

```text
深度研究一下某个产品方向的主要风险、证据来源和下一步验证方案。
```

后端会把上游步骤结果传给下游步骤。一个步骤可以调用搜索、确定性 Skill 或受限命令；综合步骤通常只消费依赖步骤的输出，不再额外调用工具。

## Shell Command 安全边界

Shell Command 是内置工具，而不是普通 Skill。它的命令由模型规划，但执行前会经过代码层硬校验：

- 只允许白名单内的只读命令，例如 `pwd`、`ls`、`cat`、`head`、`tail`、`wc`、`grep`、`rg`、`find`。
- 只允许 `&&` 连接命令，禁止管道、重定向、分号、命令替换、单独 `&` 等 shell 语法。
- 禁止绝对路径、`..` 跳出工作目录、敏感文件 token 和危险 `find` action。
- 禁止未批准解释器和包管理器。
- 每次执行有超时、输出截断和临时 `HOME`。

这是一层应用级硬约束，但不是完整 OS 级沙箱。生产或开放场景应优先使用容器隔离、低权限用户、只读挂载和人工确认。

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
- 项目可扩展到任意领域，但领域能力取决于接入的 Skill、数据源、索引和 executor。
- 当前 RAG 以文件上下文、Web Search、本地数据和 Skill 输出为主；长期记忆抽取、向量化检索和复杂权限模型仍需继续设计。
- Deep Research 会读取 Skill catalog 并自动规划，但只有注册了 executor 或受控执行路径的 Skill 才能稳定真实执行。
- 当前还没有持久化 checkpoint。刷新或进程重启后的任务恢复能力需要后续补充。
- 生成代码型 Skill 有 AST 与内置函数限制，但仍在 Python 进程内执行，不应运行不可信 Skill。
- Shell Command 已做白名单硬约束，但默认没有用户确认，也不等同于容器或系统级沙箱。
