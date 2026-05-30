# Pure Demo Script — 2-Minute Agent Runtime Backend Walkthrough

---

## Demo 目标

这不是聊天机器人演示。这是 **Agent Runtime 后端平台** 的演示。

我们要在 2 分钟内展示：
- 如何通过 FastAPI 创建 Project / Task / Run
- ToolGateway 如何治理每个工具调用
- Trace 如何让 Agent 行为完全可观测
- Evaluator 如何把运行时行为变成可验证的断言

观众：后端 / Infra / Platform 面试官。

---

## 准备环境

在录制前，打开 2 个终端窗口：

**Terminal 1 — 服务器：**

```powershell
cd D:\project\pure
.\.venv\Scripts\Activate.ps1
uvicorn pure.server.main:app --reload
```

**Terminal 2 — API 调用：**

```powershell
cd D:\project\pure
.\.venv\Scripts\Activate.ps1
```

打开浏览器：`http://127.0.0.1:8000/docs`（Swagger UI，用于展示端点列表）。

---

## 演示流程

总时长：~2 分钟

### 0:00-0:20 — 项目定位（口头介绍 + Swagger）

**画面：** 浏览器打开 Swagger UI `http://127.0.0.1:8000/docs`

**话术：**

> Pure 是一个面向研发工作流的 Agent Runtime 后端平台。
> 它不是你平时用的 Claude Code 或 Copilot —— Pure 是背后的 harness：管理 Project / Task / Run 生命周期，治理工具调用，记录可审计 trace，支持 checkpoint 恢复。
> 现在我们在 Swagger UI 可以看到所有 API 端点：projects、tasks、runs、tools、knowledge、evaluator。

---

### 0:20-0:45 — 创建 Project 和 Task

**画面：** 切换到 Terminal 2，逐条输入命令

**Step 1: 创建 Project**

```powershell
$body = @{ name = "DemoProject"; root_path = "." } | ConvertTo-Json
$project = Invoke-RestMethod -Uri http://127.0.0.1:8000/projects -Method Post -Body $body -ContentType "application/json"
$project.id
```

**话术：**

> 首先创建一个 Project。Project 对应一个代码仓库，所有 Task 和 Run 都挂在 Project 下面。
> 注意 root_path 是 `.`，Pure 限制所有文件操作不能逃逸这个根路径。

**Step 2: 创建 Task**

```powershell
$body = @{
    project_id = $project.id
    title = "Inspect repository structure"
    prompt = "List the files in this repository and read the README to understand the project."
    runtime_config = @{ max_steps = 6; approval_mode = "auto" }
    dry_run = $true
} | ConvertTo-Json -Depth 4
$task = Invoke-RestMethod -Uri http://127.0.0.1:8000/tasks -Method Post -Body $body -ContentType "application/json"
$task.id
$task.status
```

**话术：**

> Task 定义了一个要执行的 Agent 任务：标题、prompt、runtime_config。
> runtime_config 里我们设了 max_steps=6 —— 最大 6 次工具调用；approval_mode=auto —— 工具自动审批。
> dry_run=true 表示用 FakeModelClient，不调用真实 LLM，但完整走 Runtime 路径。
> task.status 现在是 "created" —— 还没有开始执行。

---

### 0:45-1:15 — Run Task 并查看 Trace

**Step 3: 启动 Run**

```powershell
$body = @{ dry_run = $true } | ConvertTo-Json
$run = Invoke-RestMethod -Uri "http://127.0.0.1:8000/tasks/$($task.id)/run" -Method Post -Body $body -ContentType "application/json"
$run.run_id
$run.status
```

**话术：**

> 调用 run 端点启动执行。注意请求立即返回，status 是 "queued" —— Task 在后台异步执行。
> 这是关键设计：Pure 的 /run 不是同步阻塞的，它创建 Run 记录后立即返回。

**Step 4: 等待完成并查看 Status**

```powershell
Start-Sleep -Seconds 4
$status = Invoke-RestMethod -Uri "http://127.0.0.1:8000/tasks/$($task.id)/status"
$status.status
$status.current_step
$status.last_trace_event.event_type
```

**话术：**

> 4 秒后轮询 status。current_step 告诉我们执行了多少步，last_trace_event 是最后一个 trace 事件类型。
> 如果 status 是 "completed"，说明 Agent 成功完成了任务。

**Step 5: 查看 Trace**

```powershell
$trace = Invoke-RestMethod -Uri "http://127.0.0.1:8000/runs/$($run.run_id)/trace"
$trace.events | Select-Object step, event_type, latency_ms | Format-Table
```

**话术：**

> 这就是 Pure 的核心能力之一：结构化 Trace。每一步发生了什么、耗时多少毫秒，全部记录在 trace.jsonl 中。
> run_started → knowledge_retrieved → context_built → model_called → tool_executed → checkpoint_created → run_completed。
> 这不是日志文件 —— 每一步都带 event_type、step、timestamp、latency_ms、status。
> 出了问题时你可以精确定位到第几步、哪个工具、耗时多少。

**Step 6: 查看 Report**

```powershell
$report = Invoke-RestMethod -Uri "http://127.0.0.1:8000/runs/$($run.run_id)/report"
$report | ConvertTo-Json -Depth 3
```

**话术：**

> Report 是这次 Run 的聚合摘要：final_output、knowledge_sources、steps、有没有错误。
> 如果面试官问"Agent 执行完你怎么复盘" —— 给他看 trace + report。

---

### 1:15-1:40 — Tool Repetition Guard 演示

**画面：** 展示一个带有 repetition guard 的 RuntimeConfig

```powershell
$body = @{
    project_id = $project.id
    title = "Repetition guard demo"
    prompt = "Read the README file three times to understand it deeply."
    runtime_config = @{
        max_steps = 6
        approval_mode = "auto"
        tool_repetition_guard = @{ enabled = $true; window = 2; mode = "block" }
    }
    dry_run = $true
} | ConvertTo-Json -Depth 5
$task2 = Invoke-RestMethod -Uri http://127.0.0.1:8000/tasks -Method Post -Body $body -ContentType "application/json"

$body2 = @{ dry_run = $true } | ConvertTo-Json
$run2 = Invoke-RestMethod -Uri "http://127.0.0.1:8000/tasks/$($task2.id)/run" -Method Post -Body $body2 -ContentType "application/json"
```

**话术：**

> 这是 Tool Repetition Guard。真实模型经常会陷入循环 —— 连续 3 次调用 read_file 读同一个文件。
> repetition guard 检测到连续 window=2 次相同 tool+args 后，mode=block 会直接拒绝执行，而不是浪费 step。
> 这个问题来自真实使用体验 —— Claude Code 的 backlog 里也有类似的 loop detection。Pure 在 Runtime 层解决了它。

---

### 1:40-2:00 — Evaluator 演示

**Step 7: 运行 Evaluator**

```powershell
$body = @{
    project_path = "."
    cases_path = "eval_cases.json"
    dry_run = $true
} | ConvertTo-Json
$eval = Invoke-RestMethod -Uri http://127.0.0.1:8000/eval/run -Method Post -Body $body -ContentType "application/json"
$eval.eval_id
$eval.summary | ConvertTo-Json -Depth 2
```

**话术：**

> 最后是 Evaluator。它不是简单的 prompt smoke test —— 它定义 JSON cases，每个 case 可以断言：
> - 哪些工具必须被调用
> - 哪些工具禁止被调用
> - 最终输出必须包含哪些关键词
> - trace 里必须出现哪些事件
> 
> Evaluator 走完整的 Runtime 路径，用 FakeModelClient 做 dry-run，不需要真实 LLM。
> summary 会告诉你 task_success 率、forbidden_tool_count、平均 steps。
> 如果有人问"你怎么保证 Agent 不会做危险操作" —— evaluator cases 就是答案。

---

### 可选的 Knowledge 演示（如果时间允许）

```powershell
# Index project docs
$body = @{ project_id = $project.id; paths = @("README.md"); reset = $true } | ConvertTo-Json -Depth 3
Invoke-RestMethod -Uri http://127.0.0.1:8000/knowledge/index -Method Post -Body $body -ContentType "application/json"

# Search
$body = @{ project_id = $project.id; query = "runtime architecture"; top_k = 3 } | ConvertTo-Json
Invoke-RestMethod -Uri http://127.0.0.1:8000/knowledge/search -Method Post -Body $body -ContentType "application/json"
```

**话术：**

> Knowledge 为 Agent 提供上下文增强。默认用 fake embeddings + JSON vector store —— 不依赖外部 API。
> 每个 Run 开始时自动检索相关知识片段，注入 prompt context，把来源写入 trace 和 report。

---

## 演示话术速查

| 时间 | 画面 | 核心信息 |
|---|---|---|
| 0:00 | Swagger UI | "Pure 是 Agent Runtime 后端，不是聊天机器人" |
| 0:20 | Terminal: POST /projects | "Project 绑定代码仓库，限制工具的文件访问范围" |
| 0:35 | Terminal: POST /tasks | "Task 定义任务：prompt + runtime_config + max_steps" |
| 0:45 | Terminal: POST /run | "Run 异步启动，立即返回 queued 状态" |
| 1:00 | Terminal: GET /status | "轮询获得当前 step 和 last_trace_event" |
| 1:10 | Terminal: GET /trace | "结构化 JSONL trace：每一步的 event_type + latency" |
| 1:20 | Terminal: GET /report | "Report 聚合摘要：final_output + sources + 错误" |
| 1:30 | Terminal: Repetition Guard | "Tool Repetition Guard 防止模型循环浪费 step" |
| 1:45 | Terminal: POST /eval/run | "Evaluator 用 JSON cases 断言运行时行为" |
| 1:55 | 总结 | "Agent Runtime 的完整链条：创建→执行→观测→验证" |

---

## 常见问题

### "如果 step limit reached 怎么解释？"

> max_steps 是 Pure 的安全边界，防止 Agent 无限循环消耗资源。达到上限后 Pure 会写 run_failed trace event 并返回已完成的 work。这本身就是 ToolGateway 设计的一部分 —— 不是 bug，是约束。

### "dry_run 和真实 provider 有什么区别？"

> dry_run 用 FakeModelClient，输出是确定性的（由 mock_outputs 控制或内置默认输出）。Trace、Report、Checkpoint 等所有 Runtime 行为完全一致。dry_run 用于测试、CI、Demo 和 Evaluator。切换到真实 provider 只需去掉 dry_run=true 并配置 .env。

### "为什么不用 Redis / Celery？"

> 当前版本定位是单机 Agent Runtime 原型。任务调度使用 asyncio + ThreadPoolExecutor 在进程内执行。Redis/Celery 在 Production Roadmap 里，但目前没有引入它们的必要 —— 这会增加部署复杂度而不改变 Runtime 核心逻辑。

### "为什么不是 Claude Code 替代品？"

> Claude Code 是交互式编码助手，解决的是"人写代码更快"。Pure 是 Agent Runtime 后端，解决的是"Agent 执行任务时的治理、观测、恢复、验证"。Pure 的 CLI 入口只是为了本地测试方便，它的核心价值在服务端。

---

## 当前限制

- 单机原型：任务执行是进程内的 asyncio + ThreadPoolExecutor，服务重启会丢失正在执行的任务。
- 无 Auth / RBAC / 多租户隔离 —— 不适合直接暴露到公网。
- 无 WebSocket / SSE 实时推送 —— 客户端需要轮询 status。
- Knowledge 默认使用 fake embeddings，真实 embedding 需要额外配置。
- 未在 SWE-bench 等标准基准上评估。
- 真实模型行为取决于 provider 和模型选择，不保证输出质量。
