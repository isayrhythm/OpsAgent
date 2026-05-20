# OpsAgent

LangGraph + FastAPI + Skill 的最小可运行骨架，支持动态装载 `skill/*.md`、DeepSeek 路由、异步任务执行和 SSE 进度推送。

## 目录

- `backend/`：FastAPI API、LangGraph 工作流、Skill 装载和执行逻辑。
- `backend/app/llm/`：模型配置、DeepSeek 请求封装和提示词。
- `frontend/`：前后端分离的静态聊天页面。
- `skill/`：Skill markdown 定义。新增 `*.md` 后，下次请求会自动扫描并参与路由。
- `memory/`：后端记忆目录。短期记忆按 `short_term/{user_id}/conversations/{session_id}.json` 落盘，上传文件预留在 `short_term/{user_id}/uploads/`，长期记忆预留在 `long_term/{user_id}/profile.json`。
- `data/`：测试数据。

## 配置 DeepSeek

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
OPSAGENT_EXECUTION_TIMEOUT_SECONDS=20
OPSAGENT_MEMORY_DIR=memory
```

后端启动时会自动读取根目录 `.env`。密钥只写在 `.env`，不要提交到版本库。

## 启动后端

```powershell
python -m pdm use 3.11
python -m pdm install
python -m pdm run api
```

如果本机还没有 Python 3.11，需要先安装 Python 3.11，再执行上面的命令。当前项目要求 `requires-python = ">=3.11"`。

## 启动前端

前端是静态页面，可以直接打开 `frontend/index.html`。如果浏览器限制本地文件请求，可以在项目根目录启动一个静态服务：

```powershell
python -m pdm run web
```

然后访问 `http://127.0.0.1:5173`。

## API

- `GET http://127.0.0.1:8001/api/health`：健康检查。
- `GET /api/skills`：实时扫描并返回当前 Skill 列表。
- `POST /api/uploads`：上传文件到短期记忆目录，返回文件 ID、文件名、类型、大小和保存路径。
- `POST /api/chat`：创建聊天任务，返回 `task_id` 和 SSE 地址。
- `GET /api/tasks/{task_id}/events`：监听任务进度和最终结果。

上传文件不会把完整文件内容塞进模型上下文。聊天请求只携带附件元信息，例如 `file_id`、`filename`、`content_type`、`size` 和后端保存路径。需要读取文件内容时，应由专门 Skill 根据路径处理。

## Skill 约定

每个 Skill 是一个 `skill/*.md` 文件，文件头部使用 frontmatter：

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

`name`、`description`、`trigger` 等元信息会被装载给路由器。只有路由命中后，后端才读取完整 Skill 文档并生成执行代码。文件正文用于指导 LLM 生成执行代码，代码需要把 JSON 可序列化结果赋值给 `result`。
