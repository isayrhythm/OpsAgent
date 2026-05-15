# OpsAgent

LangGraph + FastAPI + Skill 的最小可运行骨架，支持动态装载 `skill/*.md`、DeepSeek 路由、异步任务执行和 SSE 进度推送。

## 目录

- `backend/`：FastAPI API、LangGraph 工作流、Skill 装载和执行逻辑。
- `backend/app/llm/`：模型配置、DeepSeek 请求封装和提示词。
- `frontend/`：前后端分离的静态聊天页面。
- `skill/`：Skill markdown 定义。新增 `*.md` 后，下次请求会自动扫描并参与路由。
- `data/`：测试数据。

## 配置 DeepSeek

```powershell
Copy-Item .env.example .env
```

然后编辑项目根目录的 `.env`：

```dotenv
DEEPSEEK_API_KEY=your_deepseek_api_key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
OPSAGENT_EXECUTION_TIMEOUT_SECONDS=20
```

后端启动时会自动读取根目录 `.env`。密钥只写在 `.env`，不要提交到版本库。

## 启动后端

```powershell
pdm use 3.11
pdm install
pdm run api
```

如果本机还没有 Python 3.11，需要先安装 Python 3.11，再执行上面的命令。当前项目要求 `requires-python = ">=3.11"`。

## 启动前端

前端是静态页面，可以直接打开 `frontend/index.html`。如果浏览器限制本地文件请求，可以在项目根目录启动一个静态服务：

```powershell
pdm run web
```

然后访问 `http://127.0.0.1:5173`。

## API

- `GET /api/health`：健康检查。
- `GET /api/skills`：实时扫描并返回当前 Skill 列表。
- `POST /api/chat`：创建聊天任务，返回 `task_id` 和 SSE 地址。
- `GET /api/tasks/{task_id}/events`：监听任务进度和最终结果。

## Skill 约定

每个 Skill 是一个 `skill/*.md` 文件，文件头部使用 frontmatter：

```markdown
---
name: query_gene_expression
description: 当用户请求查询拟南芥基因表达时触发
---
```

`name` 和 `description` 会被装载给路由器。文件正文用于指导 LLM 生成执行代码，代码需要把 JSON 可序列化结果赋值给 `result`。
