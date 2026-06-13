# Agent 逻辑链与重试策略

这份文档只描述当前实现里的真实链路，不描述理想架构。

## 总览

```text
POST /api/chat
  -> 创建后台任务
  -> 前端通过 SSE 监听任务事件
  -> Agent Graph
       -> 普通回答
       -> Skill 调用
       -> Deep Research DAG
```

主入口：

```text
backend/app/agents/agent_graph.py
```

Deep Research 入口：

```text
backend/app/agents/deep_research.py
```

内置工具：

```text
backend/app/tools/web_search.py
backend/app/tools/web_search_planner.py
backend/app/tools/command_tool.py
backend/app/tools/tool_runner.py
```

## 主 Agent 链路

```text
intake_uploads
  -> load_skills
  -> route
      -> final_answer
      -> execute_skill -> final_answer
      -> research_graph
```

### intake_uploads

职责：

- 读取本轮搜索配置：`off / auto / force`。
- 调用 `plan_web_search` 判断是否需要搜索。
- 如果需要搜索，提前启动异步搜索任务。
- 整理上传文件 intake。
- 生成 `data_profiles`，供后续路由和 Skill 使用。

搜索任务在这里启动，但结果通常到 `final_answer` 阶段才等待和整合。

### load_skills

职责：

- 扫描 `skill/*.md`。
- 加载 Skill catalog。
- 每个 Skill 的 `name / description / trigger / execution_mode / executor` 会进入路由和 Deep Research planner。

### route

职责：

- 先判断是否进入 Deep Research。
- 如果不是 Deep Research，再用 Router 从 Skill catalog 里选择一个或多个 Skill。
- 如果没有选中 Skill，则走普通回答。

分支：

```text
route_mode = chat
route_mode = skill
route_mode = deep_research
```

### execute_skill

职责：

- 执行普通 Skill。
- 单个 Skill 直接执行。
- 多个 Skill 并发执行。
- Skill 执行后会进入 `evaluate_skill_result`。
- 最后交给 `final_answer` 综合回答。

### final_answer

职责：

- 普通回答：直接用普通对话 prompt。
- 搜索回答：等待 web search 结果，把 sources/evidence 传给最终回答。
- Skill 回答：把 Skill 结构化结果整理为自然语言。

## 普通回答链路

```text
用户问题
  -> intake_uploads 判断是否搜索
  -> load_skills
  -> route 判定不需要 Skill / Deep Research
  -> final_answer
       -> 如果搜索启用：等待搜索结果，作为 web_search evidence
       -> 如果搜索未启用：直接普通回答
```

普通回答不会默认调用 Shell Command。Shell Command 会作为内置 tool 进入 router 的候选目录，只有 router 明确选中 `Shell Command` 时才进入命令执行分支。

## Skill 链路

```text
用户问题
  -> load_skills
  -> route_registered_skills
  -> execute_skill
       -> deterministic_python: 调用注册 executor
       -> generated_python: LLM 生成 Python 并执行
  -> evaluate_skill_result
  -> final_answer
```

### deterministic_python

真实执行依赖两件事：

```text
skill/*.md 里声明 executor
backend/app/services/skill_runtime.py 里注册 executor
```

如果没有注册 executor，确定性 Skill 不能稳定真实执行。

### generated_python

`generated_python` 会让 LLM 根据 Skill 文档生成 Python 代码，然后由代码执行器执行。

它可以真实执行，但稳定性弱于 deterministic executor。

当前代码执行器会限制一些危险行为，例如：

- 禁止 `os / subprocess / shutil / socket / pathlib`。
- 禁止 `eval / exec / compile / open / __import__` 等危险入口。
- 禁止删除类操作。

这不是完整沙箱，只是基础限制。

## Deep Research 链路

```text
classify_intent
  -> plan_research
  -> validate_plan
  -> execute_dag
  -> evaluate_steps
  -> repair_or_continue
  -> synthesize_answer
```

### classify_intent

判断用户是否需要多步研究。

典型触发：

```text
深度研究
深入研究
系统研究
调研
综述
全面分析
deep research
literature review
```

### plan_research

输入：

- 用户问题。
- 最近历史。
- 搜索 provider。
- 内置工具列表。
- Skill catalog。

输出：

```text
3-6 个 DAG task
每个 task 有 id / title / question / purpose / dependencies / tools
```

planner 可以看到的工具：

```text
Search Query Rewriter
Tavily Search
Quark Search
Shell Command
skill/*.md 加载到的 Skill
```

### validate_plan

职责：

- 校验工具名必须存在。
- 过滤明显不该用的工具。
- 综合、总结、交叉验证类节点通常不再调用外部工具。
- 自动补一些必要节点和依赖。

当前仍有少量领域规则，例如：

```text
问题问“哪些基因 / 相关基因 / 候选基因”时，倾向补 trait2gene_query。
问题问“功能 / 注释 / 详细信息”时，倾向补 query_gene_info。
query_gene_info 会依赖上游 trait2gene_query / rice_trait_records_query。
gene_phenotype_prediction 会依赖上游 query_gene_info / trait2gene_query。
```

长期更好的方式是用 typed artifact：

```text
tool consumes gene_list
tool produces gene_profile
planner 按 artifact 类型自动接线
```

### execute_dag

按依赖执行任务：

```text
没有未完成依赖的节点可以并发执行
有依赖的节点等待上游完成
如果依赖图断裂，至少取一个剩余节点继续，避免死锁
```

每个 task 的结果会进入：

```text
evidence          # 搜索结果
skill_outputs     # Skill 结果
command_outputs   # Shell Command 结果
summary           # 当前步骤摘要
```

### evaluate_steps

判断当前 steps 是否足够回答用户问题。

如果不足，输出缺失项和可选 repair tasks。

### repair_or_continue

当前最多 repair 一轮。

```text
如果 sufficient = true：进入 synthesize_answer
如果 insufficient 且 repair_attempts < 1：补 repair task
如果 repair_attempts >= 1：停止继续 repair，进入 synthesize_answer
```

### synthesize_answer

最终回答只允许基于：

```text
research_steps
evidence
skill_outputs
command_outputs
evaluations
```

不能声称使用了未出现在这些结果里的工具、文件、实验或分析。

## Search Tool 链路

搜索是内置 tool，不是普通 Skill。

```text
用户问题
  -> 判断搜索模式 off / auto / force
  -> Search Query Rewriter
       -> 判断是否需要搜索
       -> 生成 1-5 条 query
  -> Tavily / Quark 多源搜索
  -> 去重
  -> 排序
  -> 结果不足时补一次 follow-up query
  -> evidence 进入 final_answer 或 Deep Research step
```

### 搜索模式

```text
off    明确不搜索
auto   自动判断是否搜索
force  强制搜索
```

### 多源搜索

多个 query、多个 provider 会异步执行。

```text
Tavily：更适合英文、关键词、公开网页、论文、文档。
Quark：更适合中文问题、中文来源、本地语境。
```

### 搜索结果处理

```text
合并 results
按 URL 去重
按 query term 命中、API score、query priority 排序
多 provider 时尽量保留来源多样性
```

如果结果太少，并且 query 数少于 5，会补一次 follow-up query。

## Shell Command 链路

Shell Command 是内置 tool，不是普通 Skill，但会和 Skill 一起进入 router / planner 的候选工具列表。

普通 Agent 链路里，必须由 LLM router 明确选择 `Shell Command`，不会再靠关键词规则直接触发。
Deep Research 链路里，必须由 ResearchPlanner 在 task.tools 中选择 `Shell Command`。

```text
router / ResearchPlanner 选择 Shell Command
  -> command planner 生成第 1 条命令
  -> 受控 runner 执行
  -> 保存 command / stdout / stderr / exit_code / timed_out
  -> 如果成功：结束
  -> 如果失败：
       -> 把失败结果反馈给 command planner
       -> 生成第 2 条修复命令
       -> 再执行一次
  -> command_outputs 进入下游 task 和最终综合
```

注意：这不是简单重放同一条命令，也没有离线本地兜底命令。

```text
第 1 条失败
  -> 收集失败信息
  -> planner 看失败信息
  -> 生成第 2 条修复命令
```

### 命令工具边界

默认配置：

```text
OPSAGENT_COMMAND_TOOL_ENABLED=true
OPSAGENT_COMMAND_TOOL_BACKEND=auto
OPSAGENT_COMMAND_TOOL_TIMEOUT_SECONDS=20
OPSAGENT_COMMAND_TOOL_MAX_OUTPUT_CHARS=12000
OPSAGENT_COMMAND_TOOL_WORKDIR=memory/command_tool
```

backend 选择：

```text
auto
  -> Windows 且有 WSL：wsl
  -> 有 bash：native
  -> 有 docker：docker
  -> 否则 native
```

限制：

- 默认工作目录在 `memory/command_tool`。
- 默认不暴露 `.env`。
- 阻止访问敏感文件，例如 `.env`、SSH key、credential、secret、api_key。
- 阻止网络命令，例如 `curl / wget / ssh / scp`。
- 阻止安装命令，例如 `apt / pip install / npm install`。
- 阻止危险删除和系统命令，例如 `rm -rf / rmdir / format / shutdown / reboot`。
- stdout / stderr 会截断。

## tool_runner

统一 runner 在：

```text
backend/app/tools/tool_runner.py
```

它不关心具体是搜索、Skill 还是命令，只负责：

```text
attempts
退避等待
异常型重试
结果型重试
最终错误形状
```

核心字段：

```text
max_attempts
initial_delay_seconds
backoff_multiplier
retry_exceptions
fatal_exceptions
retry_if_exception
retry_if_result
wrap_exceptions
```

含义：

- `max_attempts`：最多尝试次数。
- `retry_exceptions`：哪些异常类型有资格重试。
- `fatal_exceptions`：哪些异常永远不重试。
- `retry_if_exception`：在异常类型之外，根据细节判断是否重试，例如 HTTP status code。
- `retry_if_result`：工具没有抛异常，但返回 `status=failed` 时，可以基于结果判断是否重试。
- `wrap_exceptions`：是否把最终异常包装成 `ToolRunnerError`。老调用链依赖原始异常类型时设为 `false`。

## 当前重试策略

| 类型 | 是否走 tool_runner | 当前次数 | 重试条件 |
| ---- | ------------------ | -------- | -------- |
| Tavily / Quark HTTP 请求 | 是 | 最多 3 次 | timeout、网络错误、429、5xx |
| Tavily advanced -> basic | 是，外加本地 fallback | 400 时降级 1 次 | Tavily `advanced` 搜索 400 时改用 `basic` |
| Search follow-up query | 不算 retry，是补搜 | 最多 1 次 | 首轮结果太少且 query 数少于 5 |
| Shell Command | 是 | 最多 2 条命令 | 第 1 条失败后，用失败结果生成第 2 条修复命令 |
| Deep Research registered Skill | 是 | 1 次 | 当前不自动重试 |
| 普通 deterministic Skill | 是 | 1 次 | 当前不自动重试 |
| 普通 generated_python Skill | 外层 1 次，内部最多修复 1 次 | 首次执行 + 1 次代码修复 | 生成代码失败或结果评估为 `retry_code` |
| Deep Research repair | 不属于 tool retry | 最多 1 轮 | evaluator 判断步骤结果不足 |
| LLM JSON / 普通 LLM 调用 | 暂无统一 retry | 1 次 | 按调用点处理；router 和 command planner 失败时抛错 |

## 不重试的情况

以下情况会快速失败：

- API key 错误。
- HTTP 401 / 403。
- 明确的请求格式错误。
- Shell Command 触发安全拦截。
- Skill contract/schema 校验失败。
- 用户输入缺少必要条件。

原则：

```text
临时故障可以重试
确定性错误快速失败
命令失败修复命令，不盲目重放同一条
```

## 前端展示

Deep Research 的 plan 会通过 SSE 推给前端：

```text
research_plan
research_step
```

前端展示方式：

```text
□ / ■ 节点状态
节点标题
节点使用工具
running / done / failed
```

Shell Command 和 Skill 一样会显示在节点的工具列表里，但不会展示完整 stdout/stderr。完整结果保存在后端 task 的 `command_outputs` 中，最终回答只取必要摘要。
