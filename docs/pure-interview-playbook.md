# Pure Interview Playbook

---

## 1. 三句话定位

1. **Pure 是一个面向研发工作流的 Agent Runtime 后端平台原型** —— 它关注的是 Agent 执行任务时的治理、观测、恢复和验证，而不是辅助人类写代码。
2. **Pure 不是 Claude Code 替代品** —— 它不提供交互式编码体验，它提供的是 Runtime harness：管理 Project / Task / Run 生命周期，所有工具调用经过 ToolGateway，每步执行写入结构化 trace。
3. **核心差异点在 ToolGateway、Trace、Checkpoint、Evaluator、Knowledge 和 Tool Repetition Guard** —— 这些模块解决的是 "Agent 在生产环境里怎么安全执行、怎么审计、怎么恢复、怎么验证" 的问题。

---

## 2. 30 秒介绍

> Pure 是我做的一个 Agent Runtime 后端平台原型。大多数 Agent 项目关注的是 "模型能不能把任务做对"，但我关注的是 "Agent 执行任务的工程基础设施" —— 怎么治理工具调用、怎么记录可审计的执行过程、怎么在中断后安全恢复、怎么用 evaluator 验证运行时行为而不是只 smoke-test 一个 prompt。项目用 FastAPI + SQLAlchemy + pytest，有完整的 CLI 和 HTTP API，170+ 测试，hermetic CI 不需要真实 LLM key。

---

## 3. 2 分钟项目讲解

### 背景痛点（20s）

> 我看过很多 Agent 原型，它们普遍有几个工程问题：工具调用没有治理层，执行过程不可观测，失败后没法复盘，中断后只能重来，模型偶尔陷入循环反复调用同一个工具。更麻烦的是，这些项目的测试方式就是手工跑几个 prompt 看看输出对不对 —— 没有人验证 Agent 在运行时到底做了什么。

### Runtime 架构（30s）

> 所以我做了一个 Runtime 后端。它有一个主控制循环 PureRuntime，每次执行叫做一个 Run。Run 启动后会构建 context、检索 knowledge、调模型、执行工具、写 trace、创建 checkpoint，循环直到完成或达到步数上限。最关键的是所有工具调用都经过 ToolGateway —— 它不是随意执行，而是先检查风险等级和审批策略，执行后把审计字段写进 trace event。

### 核心模块（40s）

> ToolGateway 把工具分成 safe / medium / high 三个风险等级，支持 auto / readonly / manual 三种审批模式。每个 Run 产生一个 trace.jsonl，里面是结构化的 per-step events，包含 event_type、step、timestamp、latency_ms、status —— 这不是日志，是机器可读的审计链路。Checkpoint 不是简单存个文件 —— 它在每个 risky tool 执行前把 workspace hash、memory snapshot 和 runtime identity 快照下来。Resume 时会验证这三者是否匹配，不匹配就拒绝恢复。

### 真实问题迭代（20s）

> Tool Repetition Guard 来自真实体验 —— 我用 DeepSeek 的时候发现模型会连续三次调用 list_files 读同一个目录，白白浪费 step。于是我在 Runtime 层做了检测：如果连续 window=2 次相同 tool+args 调用，就 block 掉或 warn。这不是 prompt engineering 的 trick，而是 Runtime 层的 safeguard。

### 限制与 Roadmap（10s）

> 当前是单机原型 —— asyncio + ThreadPoolExecutor。没有 Redis / Celery / Auth / WebSocket。这些在 Roadmap 里，但我想先把 Runtime 的核心治理能力做扎实。

---

## 4. 核心模块讲解

### PureRuntime

PureRuntime 是 Agent 的主控制循环，入口是 `ask()`。一次执行的生命周期：

```
记录 user message → 创建 TaskState → RunStore.start_run()
→ Knowledge 检索 → 主循环 (max_steps 次):
  → ContextManager.build() → model_client.complete()
  → parse() → tool or final
  → if tool: ToolExecutionService.run_tool() → ToolGateway.execute()
  → if risky tool: CheckpointService.create_checkpoint()
  → 写 trace event
→ 写 report.json → 返回 final
```

**面试重点**：这不是一个 invoke-model-and-return 的薄封装。它有完整的 context build、knowledge retrieval、tool governance pipeline、trace writing、checkpoint snapshotting 和 report aggregation。

### ToolGateway

统一工具治理入口。每个工具调用都经过 `ToolExecutionService → ToolGateway.execute()`：

- **Risk levels**：safe（read_file、list_files、search）、medium（delegate）、high（write_file、patch_file、run_shell）
- **Approval modes**：auto（执行 + 记录）、readonly（拒绝 write/patch/shell）、manual（返回 waiting_approval）
- **Pre/post workspace hash diff**：对 risky 工具执行前后计算文件系统变化，写进 trace
- **审计字段**：`risk_level`、`approval_decision`、`tool_status`、`tool_error_code`、`security_event_type`、`affected_paths`、`workspace_changed`

**为什么重要**：内部 tools 和外部 MCP tools 走同一条路径。你不会问 "这个 MCP tool 是否绕过了安全策略" —— 因为它和内部 tool 经过的是完全相同的 ToolGateway。

### Trace / Report

- **trace.jsonl**：每行一个 JSON 对象。标准事件类型：`run_started`、`context_built`、`knowledge_retrieved`、`model_called`、`tool_requested`、`tool_validated`、`tool_executed`、`memory_updated`、`checkpoint_created`、`run_completed` / `run_failed` / `run_cancelled`。
- **report.json**：Run 完成后的聚合摘要 —— final_output、steps、knowledge_sources、errors、token_usage_summary。

**为什么用 JSONL**：每行独立可解析，不需要读完整文件。streaming append，不会因 crash 丢失整条 trace。可以被 evaluator、metrics、外部工具程序化消费。

### Checkpoint / Resume

不是简单的 "存文件再读回来"。Checkpoint 包含四个部分：

1. **Runtime identity**：session_id、task_id、run_id、step —— 知道从哪里恢复
2. **Workspace fingerprint**：file hash snapshot —— 检测文件是否被外部修改
3. **Memory snapshot**：working memory + durable memory —— Agent 不能 "失忆"
4. **Last trace event**：精确的恢复点

Resume 时验证：
- 检查 schema version 匹配
- 计算当前 workspace hash 与 checkpoint hash 比对
- 验证 runtime metadata 兼容

任何一个不匹配 → 拒绝恢复。这是 identity-verified recovery，不是 blind restore。

### Knowledge

文档索引与检索，用于 Runtime context augmentation。

- **默认**：fake embeddings（确定性 mock）+ JSON/in-memory vector store —— hermetic，零依赖
- **可选**：FAISS 后端（`pip install -e ".[faiss]"`）
- **流程**：load document → split to chunks → embed → store in vector store → retrieve by query
- **检索结果**写入 trace（`knowledge_retrieved` event）和 report（`knowledge_sources`）

**为什么不是 RAG 聊天产品**：Knowledge 只在 Runtime context build 时自动注入 —— 它不是用户可交互的问答系统，是 Agent 执行前的上下文增强。

### Evaluator

不是 prompt smoke test。是运行时行为验证器。

- **cases 定义**（`eval_cases.json`）：每个 case 包含 prompt、expected_tools、forbidden_tools、success_keywords、expected_trace_events、mock_outputs
- **执行**：EvaluatorRunner 通过 RuntimeService 走完整 Runtime 路径（project → task → run → trace → report）
- **断言**：检查 tool calls 是否匹配、trace events 是否出现、forbidden tools 是否被调用、final output 是否包含 success keywords
- **输出**：结构化 report，告诉你哪个 case 因为什么原因失败，不只是 pass/fail

**和 pytest 的区别**：pytest 验证函数返回值。Evaluator 验证 Agent 的运行时行为 —— 它调了哪些工具、有没有碰危险操作、trace 对不对。

### Tool Repetition Guard

真实问题的产物。用 DeepSeek 的时候发现模型会连续多次调用同一个工具：

> list_files(".") → list_files(".") → list_files(".")

Repetition Guard 在 ToolExecutionService 中检测：

- **window**：滑动窗口大小（默认 2）
- **mode**：`warn`（只写 trace warning）或 `block`（拒绝执行）
- **检测方式**：对 consecutive tool calls 做 normalized args 比较

这不是 prompt engineering。它不修改 prompt、不调 temperature、不换模型。它在 Runtime 层阻止浪费。

### FastAPI / Task / Run 生命周期

- **Project**：绑定代码仓库路径，定义工具可访问的根目录
- **Task**：一个可执行任务（prompt + runtime_config + dry_run flag）
- **Run**：Task 的一次执行实例，产生 trace + report
- **Session**：持久化的对话状态（可恢复、可继续对话）

生命周期状态机：`created → queued → running → completed | failed | cancelled`

- `POST /tasks` → metadata only（status: created）
- `POST /tasks/{id}/run` → 创建 run，异步执行，立即返回（status: queued）
- `GET /tasks/{id}/status` → 轮询 current_step 和 last_trace_event
- `GET /runs/{id}/trace` → 结构化 trace events
- `GET /runs/{id}/report` → 聚合 report

### FakeModelClient 与真实 Provider

- **FakeModelClient**：返回确定性输出（由 mock_outputs 控制或内置默认值），不调任何外部 API
- **真实 provider**：OpenAI-compatible（`/v1/responses`）、Anthropic-compatible（`/v1/messages`）、DeepSeek（复用 Anthropic client）、Ollama（`/api/generate`）

**关键**：dry_run 和真实 provider 走完全相同的 Runtime 路径。Trace、Report、Checkpoint、Evaluator 行为一致。差别只在 model_client.complete() 的返回值来源。

---

## 5. 高频面试问题（20 个）

### Q1: Pure 解决什么问题？

**简短回答**：大多数 Agent 原型缺少工具治理、执行观测、中断恢复和运行时验证。Pure 在 Runtime 层解决这四件事。

**深挖回答**：我看过的 Agent 项目通常只关心 "prompt → LLM → 输出"，工具调用就是 `subprocess.run()` 或 `open("file").write()` 这种直接操作。一旦出错，你只有模型输出的 final answer，不知道中间发生了什么。而且没有人能回答 "Agent 执行过程中有没有做不安全的事"。Pure 的 ToolGateway + Trace + Checkpoint + Evaluator 就是为这四个 gap 设计的。

**不能说的话**：
- "现有 Agent 框架都很烂"（攻击性太强）
- "Pure 解决了 Agent 的所有问题"（夸大）
- "Pure 是下一代 Agent 基础设施"（太虚）

---

### Q2: 为什么不是普通 Agent Demo？

**简短回答**：Demo 只展示 "能跑"，Pure 展示的是 "能治理、能观测、能恢复、能验证"。

**深挖回答**：一个 Agent Demo 的标准形态是 CLI 接收 prompt → 调 LLM → 打印结果。Pure 的区别在于：它有 Task/Run 生命周期管理（不是一次性的），所有工具调用有治理层（不是裸执行），每步有结构化 trace（不是 print 日志），支持中断恢复（不是重头开始），有 evaluator 做运行时验证（不是手工看输出）。这些层是一个 "后端平台" 的基本功，不是 Demo 的附加项。

**不能说的话**：
- "别人的项目都是玩具"（不尊重同行）
- "Pure 是唯一做这些的"（不是事实）

---

### Q3: 和 Claude Code / Cursor / OpenHands 区别？

**简短回答**：那些是交互式编码助手，Pure 是 Agent Runtime 后端。不同品类。

**深挖回答**：Claude Code 解决 "人写代码更快"，Cursor 解决 "IDE 里的 AI 补全"，OpenHands 解决 "AI 软件工程师"。Pure 解决的是 "Agent 在执行任务时谁来治理工具调用、谁来记录执行过程、谁来保证中断后可恢复、谁来验证运行时行为"。Pure 不提供 IDE、不提供聊天界面、不写代码 —— 它提供 Agent 运行的 harness。

**类比**：如果说 Claude Code 是一辆车的驾驶座，Pure 就是这辆车的 ECU（发动机控制单元）和黑匣子。用户不直接和 Pure 交互，Agent 在 Pure 上运行。

**不能说的话**：
- "Pure 比 Claude Code 更好"（品类不同，无法比较）
- "Claude Code 没有这些功能"（Claude Code 有自己的安全机制）

---

### Q4: 为什么说 Pure 是 Runtime，而不是 Agent？

**简短回答**：Agent 是 "能自主执行任务的 AI"，Runtime 是 "Agent 执行任务时的环境基础设施"。

**深挖回答**：Agent 的价值在于 "聪明"—— 选什么工具、怎么推理、什么时候停。Runtime 的价值在于 "可靠"—— 工具调用是否合规、执行过程是否可审计、中断后能否安全恢复、行为能否被程序化验证。Pure 的 FakeModelClient 最能说明这一点：它用假模型输出也能跑完整的 Runtime 路径，产生 trace、report、checkpoint —— 这证明 Runtime 是独立于模型智能的基础设施层。

**不能说的话**：
- "Agent 不重要，Runtime 才重要"（两者互补）
- "Pure 可以替代任何 Agent"（Pure 是 Runtime，不是 Agent）

---

### Q5: ToolGateway 为什么要独立？

**简短回答**：如果工具治理逻辑散落在各个 tool runner 里，再加一个新 tool 或接入一个外部 MCP tool 时就没有统一的安全边界。

**深挖回答**：ToolGateway 是单一入口。每一个工具调用 —— 不管是内部的 read_file 还是外部 MCP server 注册的 tool —— 都经过 `ToolExecutionService → ToolGateway.execute()`。这意味着：风险等级是统一的、审批策略是一致的、审计字段是标准化的、pre/post diff 是所有 risky tool 共享的。如果以后要加 audit log 持久化、approval continuation API、per-tool rate limit —— 全部加在 ToolGateway 一层，不需要改任何一个 tool runner。

**不能说的话**：
- "其他项目都没有工具治理"（很多项目有，只是形式不同）
- "ToolGateway 是业界最佳实践"（缺乏外部验证）

---

### Q6: 如何防止危险工具调用？

**简短回答**：三层防护 —— risk level 分类 + approval mode 控制 + pre/post workspace diff 审计。

**深挖回答**：
1. **Risk level**：每个 tool 在 registry 中声明 safe / medium / high
2. **Approval mode**：auto（执行）、readonly（拒绝 write/patch/shell/delete）、manual（返回 waiting_approval）
3. **Snapshot diff**：risky 工具执行前后计算 workspace file hash 变化，写入 trace 的 `affected_paths` 和 `workspace_changed`

三层叠加的效果是：你知道每个工具的风险等级、你知道当前策略是否允许它执行、执行后你能审计它改了什么。在 readonly 模式下，即使模型输出了 write_file 指令，Runtime 也会拒绝执行并记录 security_event。

**不能说的话**：
- "Pure 可以完全防止 Agent 做坏事"（技术手段有限，不能防 prompt injection 导致的推理层面问题）
- "三层防护万无一失"（绝对化措辞）

---

### Q7: Trace 和普通日志有什么区别？

**简短回答**：日志是给人看的文本，Trace 是给系统消费的结构化事件。

**深挖回答**：
- 日志：`"2024-01-01 10:00:00 INFO Called read_file with path=README.md"` —— 文本，需要 regex 解析
- Trace：`{"run_id":"run_...","step":3,"event_type":"tool_executed","payload":{"name":"read_file","args":{"path":"README.md"}},"latency_ms":12,"status":"ok"}` —— 结构化，机器可直接消费

Pure 的 trace 的优势：
- Evaluator 用 trace events 做断言（expected_trace_events）
- Metrics 用 trace 计算平均 steps、平均 latency
- 每一行是独立的 JSON 对象（JSONL），支持 streaming append 和逐行解析
- 即使进程 crash，已写入的事件不丢失（不像完整的 JSON array 需要正确闭合）

**不能说的话**：
- "日志没有用"（日志有自己的场景）
- "所有项目都应该用 JSONL trace"（取决于需求）

---

### Q8: 为什么 Trace 用 JSONL？

**简短回答**：每行独立可解析、支持 streaming append、crash-safe。

**深挖回答**：如果 trace 是单个 JSON 文件，你需要 `[` 开头、每个事件后加逗号、`]` 结尾。如果进程在写入中途 crash，整个文件可能不可解析。JSONL 每行是一个完整的 JSON 对象，append-only：打开文件、写一行、flush。任何时候你读到的行都是完整的。而且对于长时间运行的 Agent，你可以 tail -f trace.jsonl 实时观察进程。

**不能说的话**：
- "JSON 格式不适合 trace"（JSON 没问题，JSON array 有问题）
- "JSONL 是最好的 trace 格式"（protobuf/avro 也有其场景）

---

### Q9: Checkpoint/Resume 怎么保证安全？

**简短回答**：三重验证 —— schema version + workspace hash + runtime identity。

**深挖回答**：
1. **Schema version**：checkpoint 的序列化格式版本。如果 Pure 升级改变了 checkpoint 结构，旧 checkpoint 不会被错误加载。
2. **Workspace hash**：checkpoint 创建时对工作区文件做 fingerprint。Resume 时重新计算 hash 比对。不匹配 → 文件被人改过 → 拒绝恢复。
3. **Runtime identity**：session_id、task_id、run_id、step。确保恢复的是同一次执行的同一个阶段。

这不是 "把 JSON 存下来下次读回来"。这是 identity-verified recovery：checkpoint 不仅包含数据，还包含对恢复条件的验证。

**不能说的话**：
- "Pure 的 checkpoint 和数据库的 WAL 一样可靠"（数据库 WAL 有更严格的 ACID 保证）
- "可以任意时间点恢复"（只在 risky tool 执行前做 checkpoint）

---

### Q10: Tool Repetition Guard 为什么需要？

**简短回答**：真实模型（尤其是 DeepSeek）会陷入循环，连续用相同参数调用同一工具。这是 prompt 无法彻底解决的问题。

**深挖回答**：这是我做真实 provider 测试时直接遇到的问题：DeepSeek 在一次运行中连续 3 次 `list_files(".")`，消耗了 max_steps 的一半。我研究了一下发现这不是孤例 —— Claude Code 的 changelog 里也有类似的 loop detection 功能。区别在于他们是在产品层解决，我是在 Runtime 层解决。Repetition Guard 不依赖模型、不修改 prompt、不调 temperature —— 它纯在工具执行前做 normalized args 比对。

**不能说的话**：
- "DeepSeek 有问题"（不针对特定模型）
- "Prompt 可以完全解决循环问题"（prompt 缓解但不根治）

---

### Q11: normalized args 怎么做？

**简短回答**：把 tool arguments 序列化为稳定的字符串形式，再比较。

**深挖回答**：
- 对 dict 类型的 args 做 sorted key 序列化
- 忽略不影响语义的差异（如 `{"path": "."}` vs `{"path":"./"}` 这种 path 差异 —— 当前版本做 exact string match，未来可用 path 规范化）
- 在 sliding window 内比较：如果 `window=2`，只比较当前调用和前两次调用

**技术细节**：当前实现用的是 `json.dumps(args, sort_keys=True)` 做比较。这保证了 `{"a":1,"b":2}` 和 `{"b":2,"a":1}` 算相同调用。

**不能说的话**：
- "normalization 可以处理所有等价调用"（path 规范化等边界情况尚未处理）
- "这个算法很复杂"（它实际上很简单，这正是它的优点）

---

### Q12: Knowledge 在 Pure 里是什么角色？

**简短回答**：Runtime context augmentation —— 在每次 Run 开始时自动检索相关文档片段注入 prompt context。

**深挖回答**：Knowledge 不是面向用户的产品功能 —— 用户不能 "问 Knowledge 一个问题"。它是 Runtime 的内部组件：context build 阶段调用 `KnowledgeService.retrieve()`，基于 user message 检索相关文档片段，生成 `knowledge_context` section 注入 prompt。检索来源写入 trace（`knowledge_retrieved` event）和 report（`knowledge_sources`）。

默认用 fake embeddings 是因为：测试和 CI 不需要外部 API，且输出确定可验证。真实场景可以配置 embedding provider。

**不能说的话**：
- "Pure 有 RAG 功能"（它更像是 "自动补全上下文"，不是交互式 RAG 产品）
- "Knowledge 检索效果很好"（fake embeddings 只是确定性的 mock，不是语义检索）

---

### Q13: Evaluator 怎么设计？

**简短回答**：JSON cases 定义期望行为 → 走完整 Runtime 路径 → 对 trace + report 做断言 → 生成结构化报告。

**深挖回答**：
1. **Cases 定义**：每个 case 指定 prompt、预期 tools、禁止 tools、成功关键词、预期 trace events、mock_outputs
2. **执行**：EvaluatorRunner 为每个 case 创建 project → task → run，通过 RuntimeService 走完整 Runtime 路径
3. **断言**：检查 tool call 列表、trace event 列表、final output 内容
4. **报告**：per-case 结果 + 聚合 metrics（task_success_rate、forbidden_tool_count、average_steps、average_latency 等）

设计关键点：Evaluator 不直接调用 PureRuntime.ask()。它走 RuntimeService（HTTP 层的 orchestration adapter），这意味着 evaluator 覆盖了从 API → service → runtime 的完整链路。

**不能说的话**：
- "Evaluator 可以替代 pytest"（互补关系）
- "Evaluator 可以跑 SWE-bench"（不支持 SWE-bench 格式）

---

### Q14: dry_run 和真实 provider 有什么区别？

**简短回答**：区别只在 model_client.complete() 的返回值来源 —— FakeModelClient 返回确定性输出，真实 provider 调外部 API。其余 Runtime 行为完全一致。

**深挖回答**：FakeModelClient 有两种模式：
1. `mock_outputs` 不为空：按顺序消费数组中的输出（`["<tool>...</tool>", "<final>Done.</final>"]`）
2. `mock_outputs` 为空：返回内置默认 final 响应

无论是哪种模式，Runtime 的 context build、knowledge retrieval、tool execution、trace writing、checkpoint creation、report generation 完全一致。这意味着：
- 测试的 trace/report 结构和真实运行相同
- Evaluator 的 dry_run 结果可以验证 Runtime 路径的正确性
- CI 不需要 LLM API key

**不能说的话**：
- "dry_run 和真实运行一模一样"（模型输出质量不同）
- "FakeModelClient 可以模拟任何模型行为"（它是确定性的，不能模拟推理差异）

---

### Q15: 为什么用 FakeModelClient 做测试？

**简短回答**：hermetic、确定性、零成本、可重复。

**深挖回答**：
- **Hermetic**：不依赖外部 API，CI 在任何环境都能跑
- **确定性**：同样的 mock_outputs 产生同样的 trace/report，断言不会 flaky
- **零成本**：不需要付 API 费用
- **可重复**：git bisect 时不会因为模型行为变化引入噪声

而且 FakeModelClient 测试的是 Runtime 路径的正确性，不是模型的质量。模型质量测试应该用 evaluator + 真实 provider，那是不同的测试维度。

**不能说的话**：
- "FakeModelClient 可以替代真实模型测试"（不能）
- "我的测试覆盖了模型质量"（FakeModelClient 测试覆盖的是 Runtime 正确性）

---

### Q16: 为什么不用 LangChain / AutoGPT？

**简短回答**：LangChain 是 Agent 框架，AutoGPT 是 Agent 应用，Pure 是 Agent Runtime 后端。不同层级。

**深挖回答**：LangChain 解决 "怎么快速搭建一个 Agent"，提供了 chain、agent、tool 等抽象。AutoGPT 解决 "让 GPT 自主完成复杂任务"。Pure 解决 "Agent 执行任务时的治理、观测、恢复、验证"。

Pure 可以用 LangChain 作为它的模型/工具抽象层（或者不用），这是实现细节。Pure 的价值不在 "怎么调用 LLM"，而在 "调完 LLM 之后发生了什么"。

**不能说的话**：
- "LangChain 设计不好"（品味问题，不是技术事实）
- "Pure 比 LangChain 更好"（不同品类，无法比较）

---

### Q17: 为什么不用 Redis/Celery？

**简短回答**：当前是单机原型，任务是 asyncio + ThreadPoolExecutor 进程内执行。Redis/Celery 在 Roadmap 里，但先在单进程内把 Runtime 逻辑做扎实比引入分布式复杂度更重要。

**深挖回答**：引入 Redis/Celery 会带来：broker 部署、worker 管理、任务序列化、重试策略、死信队列、监控面板。在当前阶段，这些复杂度会稀释 Runtime 核心逻辑的展示。Pure 的定位是 "Runtime 工程能力展示" 而不是 "分布式系统展示"。Roadmap 里写了 Redis/Celery，但需要在一个真实的部署场景下才值得引入。

**不能说的话**：
- "Redis/Celery 不需要"（它们在生产环境是必要的）
- "我的项目是分布式的"（不是）

---

### Q18: 为什么没有 Auth/WebSocket？

**简短回答**：单机原型的定位决定 —— 先做好 Runtime 核心，再补外围。Auth/WebSocket 在 Roadmap 里。

**深挖回答**：Auth 需要用户模型、token 管理、权限模型 —— 在没有明确的 multi-user 场景前，做 Auth 就是过度设计。WebSocket/SSE 需要处理连接管理、重连、背压 —— 在 poll-based 的 status 端点已经能满足当前需求时，引入流式推送的性价比不高。Roadmap 里都写了，但要等到具体的部署场景出现才有实现的上下文。

**不能说的话**：
- "Auth 不重要"（在真实产品中至关重要）
- "WebSocket 太复杂不值得做"（在需要实时推送时是必要的）

---

### Q19: 是否跑过 SWE-bench？

**简短回答**：没有。Pure 展示的是 Runtime 工程能力，不是 Agent benchmark 分数。

**深挖回答**：SWE-bench 评估的是 Agent（LLM + 工具）解决真实 GitHub issue 的能力。Pure 作为 Runtime，它的价值在于 "无论用什么模型，都能治理、观测、恢复、验证"。Roadmap 里有 SWE-bench Lite adapter，目的是让 Pure 可以作为 harness 适配标准 benchmark —— 这不是为了让 Pure 参加 benchmark，而是为了证明 Runtime 架构支持标准化评估。

**不能说的话**：
- "SWE-bench 不重要"（它是重要的 Agent 能力指标）
- "Pure 在 SWE-bench 上表现很好"（没跑过）
- "我很快就会跑 SWE-bench"（需要实际做才能说）

---

### Q20: 离生产级还差什么？

**简短回答**：任务队列（Redis/Celery）、认证（Auth/RBAC）、实时推送（WebSocket/SSE）、可观测（OpenTelemetry）、密钥管理（Secret Manager）、多租户隔离、生产级部署（K8s）。

**深挖回答**：这七个方面我都列在 Roadmap 里了。排序上：
1. **任务队列**最高优先级 —— 当前 asyncio + ThreadPoolExecutor 进程内执行，服务重启丢任务
2. **Auth/RBAC** 第二 —— 一旦暴露到内网就需要
3. **WebSocket/SSE** 第三 —— poll-based status 在单用户场景够用
4. **OpenTelemetry + 结构化日志** 第四 —— 排障的基础设施
5. **Secret Manager** 第五 —— 替换 .env
6. **多租户** 第六 —— 需要 Auth 先做完
7. **K8s** 最后 —— 部署而非功能

**不能说的话**：
- "这些都不难，很快就能做完"（低估工程难度是大忌）
- "Pure 已经是生产级了"（不是）

---

## 6. 十个最容易被拷打的问题

### 1. 和 Claude Code 区别

**拷打风险：高。** 面试官可能用 Claude Code，会直接比较。

**回答策略**：
> Claude Code 解决 "我怎么更快写代码"，Pure 解决 "Agent 执行任务时谁来治理"。品类不同。Claude Code 的产品形态是 CLI + IDE 集成，Pure 的产品形态是 FastAPI 后端 + Runtime harness。如果你让我在 Claude Code 里跑一个需要 20 步、涉及危险文件操作、要求可审计的任务 —— 这不是 Claude Code 设计的场景。如果你让我用 Pure 帮你写一个 React 组件 —— 这也不是 Pure 设计的场景。

**不能说的**：任何贬低 Claude Code 的话。

---

### 2. 未跑 SWE-bench

**拷打风险：高。** 行业标配。

**回答策略**：
> SWE-bench 评估的是 Agent + LLM 的组合能力。Pure 展示的是 Runtime 的工程能力 —— 这两者不冲突。Roadmap 里有 SWE-bench Lite adapter，目的是让 Pure 作为 harness 适配标准 benchmark，验证 Runtime 架构的通用性。但 Pure 本身不靠 benchmark 分数来证明价值。

**不能说的**："我很快就去跑"（除非你真的马上做）。

---

### 3. 无分布式任务队列

**拷打风险：高。** 后端面试官的核心关注。

**回答策略**：
> 当前用的是 asyncio + ThreadPoolExecutor 进程内执行。这是一个刻意的选择：先在单进程内把 Runtime 的生命周期管理做扎实，再引入分布式复杂度。如果我一开始就上 Celery，那 broker 部署、任务序列化、重试策略、死信队列这些问题会稀释 Runtime 核心逻辑的展示。

**不能说的**："Redis/Celery 是过度设计"（在生产环境不是）。

---

### 4. 无 Auth/RBAC

**拷打风险：中高。** 所有后端项目的必考题。

**回答策略**：
> Auth 需要用户模型、认证方式、权限模型这些上下文。单机原型阶段，做 Auth 就像给还没上路的车买保险 —— 你可以买，但不如先把引擎做好。Roadmap 里写了，等到有真实的 multi-user 部署场景再实现。

**不能说的**："Auth 很简单"（做对不简单）。

---

### 5. 默认 fake embedding

**拷打风险：中。** 面试官可能理解为 "没有真正的 RAG"。

**回答策略**：
> 默认 fake embeddings 是一个刻意设计：测试和 CI 不需要外部 API，输出确定可验证。Knowledge 的架构是可插拔的 —— VectorStore 是抽象接口，默认 InMemoryVectorStore 用 JSON 持久化，可选 FAISS 后端。真实 embedding 只需要配置 provider 和 API key。这和外部的 embedding service 的集成不是技术难点，所以我优先把架构做对，而不是赌在某个具体的 embedding provider 上。

**不能说的**："fake embedding 和真实 embedding 效果一样"（不一样）。

---

### 6. 单机原型限制

**拷打风险：高。** "这能叫后端吗？"

**回答策略**：
> 是的，当前是单机原型。准确地说，是一个 "有完整后端分层（FastAPI + Services + Repository + DB + CLI）的单机 Agent Runtime 原型"。它展示了后端工程师的系统设计能力 —— 分层、边界、测试、CI —— 而不是分布式系统能力（那是下一步）。如果你想考验我的分布式系统设计能力，我们可以聊 Roadmap 里的 Redis/Celery 方案。

**不能说的**："单机就够了"（显然不够）。

---

### 7. 真实 provider 不稳定

**拷打风险：中。** 面试官可能期望 "端到端能跑"。

**回答策略**：
> 真实 provider 的行为取决于模型。Pure 的 API 和 Runtime 路径不依赖具体 provider —— FakeModelClient 和真实 provider 走完全相同的执行路径。Provider 的稳定性不是我作为 Runtime 开发者能控制的。我能控制的是：当 provider 出错时，Runtime 怎么处理 —— 写 run_failed trace event、记录错误信息、保留已完成的步骤。

**不能说的**：甩锅给模型厂商。

---

### 8. 不是多智能体

**拷打风险：中。** "多 Agent 协作" 是热点。

**回答策略**：
> Pure 有一个 `delegate` 工具，允许 Agent 派生子 Agent 做只读调查（受 depth 限制）。这不是多智能体协作系统，而是一种受控的任务分解。完整的多 Agent 协作需要 Agent 间的通信协议、任务分配、结果合并 —— 这些在当前版本中没有。在单 Agent 治理还没做扎实之前做多 Agent 是过早扩展。

**不能说的**："Pure 支持多智能体"（delegate 不是 multi-agent）。

---

### 9. 不是生产级平台

**拷打风险：高。** README 和 Roadmap 诚实写了，但面试官一定会问。

**回答策略**：
> README 里明确写了 "Not a production distributed platform"。这是一个展示 Runtime 工程能力的原型，不是一个可以承受生产流量的平台。七个缺失项（任务队列/Auth/Streaming/Observability/Secret/Multi-tenant/K8s）都在 Roadmap 里。

**不能说的**：回避问题或模糊 "生产级" 的定义。

---

### 10. 项目来自 Pico 魔改，怎么证明是自己做的

**拷打风险：高。** 这是关键问题。

**回答策略**：
> Pico（原项目名）和 Pure 的 git history 是连续的（可以在 commit history 里看到）。关键模块 —— ToolGateway、TraceService、CheckpointService、Evaluator、Repetition Guard、Knowledge、server API、DB repositories、Docker Compose —— 都是从头设计和实现的。如果你看 git log，可以看到从 2026-05-25 到现在的 commit 记录，包括架构文档中每个 Phase Update 对应的代码变更。最重要的是：我可以逐行解释任何模块的设计决策和实现细节。

**不能说的**：
- 含糊其辞
- 声称 "全部从头写的"（工具 runner、CLI 入口等有保留和重构）
- 无法回答 git blame 的问题

---

## 7. 不能夸大的表达

| 禁止措辞 | 正确说法 |
|---|---|
| "生产级分布式 Agent 平台" | "单机 Agent Runtime 后端平台原型" |
| "替代 Claude Code / Copilot" | "不是 Claude Code 替代品，是 Runtime harness" |
| "对标 Devin" | "未在 SWE-bench 评估，不标榜 benchmark 水平" |
| "SWE-bench SOTA" | "未跑过 SWE-bench" |
| "企业级安全认证" | "ToolGateway 提供工具治理，无 Auth/RBAC" |
| "Redis/Celery 已实现" | "Roadmap 中，当前是 asyncio + ThreadPoolExecutor" |
| "多租户 SaaS" | "无多租户隔离" |
| "实时流式推送" | "poll-based status API，WebSocket/SSE 在 Roadmap" |
| "OpenTelemetry 可观测" | "结构化 JSONL trace + report，OTel 在 Roadmap" |
| "支持大规模并发" | "单机原型，进程内执行" |

---

## 8. 面试主动引导亮点

### 亮点 1：Tool Repetition Guard 来自真实问题

> 我在用 DeepSeek 做真实测试时，发现模型会连续三次 `list_files(".")`。这不是 prompt 能解决的问题 —— 你改了 prompt 说 "不要重复"，它可能在另一个场景下重复别的工具。所以我在 Runtime 层做了 Repetition Guard：检测连续相同 tool+args，支持 warn 或 block。这不是拍脑袋想出来的功能，是踩了坑之后做的。

**为什么有效**：证明你有 "发现问题 → 在正确的层解决 → 不 hack prompt" 的工程判断力。

---

### 亮点 2：Trace/Event 被 Evaluator 消费

> Trace 不是只是给人看的日志。Evaluator 直接用 trace events 做断言 —— 比如 `expected_trace_events: ["run_completed", "knowledge_retrieved"]`。这意味着 trace 是 machine-readable 的审计链路，不是 debug 用的 print。这个设计让 "验证 Agent 行为" 变成了可自动化的过程。

**为什么有效**：展示系统化思维 —— 一个模块的输出是另一个模块的输入。

---

### 亮点 3：ToolGateway 隔离模型和危险工具

> 模型输出 `{"tool":"run_shell","args":{"command":"rm -rf /"}}` 的时候，ToolGateway 已经在模型和工具之间了。它先看 approval_mode：如果当前是 readonly，就直接拒绝，不会把命令传给 subprocess。它再看 risk_level：如果是 high risk，且 approval_mode=manual，就返回 waiting_approval 而不是执行。模型不需要知道安全策略 —— 安全策略在 Runtime 层。

**为什么有效**：展示分层解耦 —— 模型只管推理，Runtime 管安全。关注点分离是资深工程师的标志。

---

### 亮点 4：Checkpoint 不是简单存文件

> 很多项目的 "恢复" 就是存一个 JSON 下次读回来。Pure 的 checkpoint 存了 workspace hash、memory snapshot、runtime identity。Resume 时先验证三项是否匹配 —— 如果有人在 Agent 暂停时手动改了文件，workspace hash 不匹配，resume 会被拒绝。这不是 "保存进度"，这是 "验证恢复条件"。

**为什么有效**：展示你对 "安全恢复" 的理解超越了序列化/反序列化。

---

### 亮点 5：Knowledge 是 runtime context augmentation，不是 RAG 产品

> Pure 的 Knowledge 不像典型的 RAG 应用 —— 用户不能 "问它一个问题"。它在每次 Run 启动时自动检索相关文档，注入 prompt context。检索来源写入 trace 和 report。默认用 fake embeddings 是为了 hermetic 测试 —— 这是刻意设计，不是没做完。

**为什么有效**：展示你对功能边界的清晰认知 —— 不是什么都要做成用户可交互的产品。Runtime 内部组件和用户产品功能是不同的东西。

---

### 亮点 6：FakeModelClient 让 Runtime 可独立验证

> 即使用假模型，Pure 也能跑完整的 Trace、Report、Checkpoint、Evaluator 路径。这证明 Runtime 不是寄生在 LLM 上的薄壳 —— 它是一个独立的基础设施层。这和测试驱动开发里的 mock 思路一致：你 mock 掉外部依赖，验证你自己的逻辑是否正确。

**为什么有效**：展示测试思维和架构解耦能力。

---

### 亮点 7：docs/ 下有完整的架构文档

> 项目不只是代码。docs/ 下有 architecture.md、api-contract.md、tool_gateway.md、runtime.md、evaluator.md、knowledge.md、mcp-client-integration.md、demo.md。这些文档描述了每个模块的设计决策、API contract 和当前限制。我写这些是因为：一个工程师如果不能用文字清晰地描述自己的设计，要么是没想清楚，要么是别人没法接手。

**为什么有效**：展示软技能 —— 文档能力、沟通能力、可维护性意识。

---

## Pure Benchmark Questions

### Q1：你的压缩率怎么测的？

我把同一批 benchmark case 分成 baseline 和 reduced 两种 context 配置跑。baseline 保留更多上下文，reduced 开启上下文治理，然后记录 Runtime 实际构造出来的 prompt 字符数。公式是：

```text
compression_rate = (baseline_prompt_chars - reduced_prompt_chars) / baseline_prompt_chars
```

这次本地结果是 12 个 case，平均 baseline prompt 7162.33 chars，平均 reduced prompt 2339.08 chars，平均字符压缩率 65.79%。我用 chars 是因为不同模型 tokenizer 不一样，字符数是跨模型 proxy；它不等于 token，也不能直接说 token 成本降低。

我不会只看压缩率。压缩如果删掉 current request 就没有意义，所以报告同时看 verifier pass rate、current request preserved rate 和 normal final rate，这三项本轮都是 100%。结果文件在 `benchmarks/pure/_runs/latest/context_compression/results.json`，汇总在 `docs/benchmarks/pure-benchmark-results.md`。当前限制是 case 数量有限、使用 FakeModelClient，而且有一个 negative compression case 被保留，所以只能说这是 runtime context behavior benchmark。

### Q2：Tool Repetition Guard 怎么证明有效？

我做的是 guard off / warn / block 三组对照，所有 case 使用同一批 mock outputs，只改 guard 配置。guard off 下 `repeated_tool_call_count` 是 10；warn 下 repeated count 仍是 10，但记录了 10 个 `repeated_tool_call_detected` event；block 下 repeated executed calls 降到 0，并记录 10 个 `tool_rejected_repeated_call` event。

检测方式是同工具名 + normalized args 的短窗口比较。路径会归一化，比如 `pure`、`.\pure`、`pure/` 会被看成同一逻辑路径，同时忽略 timeout、limit、max_lines 这类非语义参数。warn 只告警不阻断，block 会拒绝重复工具执行。本轮 block 的 `step_saving_estimate` 是 0，因为固定 mock sequence 的步数形态没有变化，所以我只说重复执行被阻断，不说节省了 step。它不能解决所有 Agent loop，也不能替代 planner。

### Q3：Tool Governance Benchmark 测什么？

它测 ToolGateway 作为模型和本地 workspace 之间的治理边界。case 包括 readonly 下允许 read/list/search，拒绝 write/patch/shell；拒绝 parent path escape 和 absolute path escape；拒绝 invalid tool name、missing arg、wrong arg type；并验证 risky write/patch 是否记录 workspace diff。

本轮 15 个 offline case 里，`policy_pass_rate`、`unsafe_rejection_rate`、`safe_allow_rate`、`workspace_escape_block_rate`、`readonly_block_rate`、`risky_tool_audit_coverage` 都是 100%，false allow 和 false reject 都是 0。risky tool audit 会检查 `workspace_changed`、`affected_paths` 等 audit fields。这不是生产级 sandbox，也不能防所有命令风险，只能说当前 ToolGateway 的本地策略和 audit contract 被 benchmark 覆盖到了。

### Q4：Checkpoint Resume Benchmark 测什么？

它测的是恢复状态识别，不是分布式事务恢复。case 覆盖 clean resume、partial stale、workspace mismatch、schema mismatch、runtime identity mismatch、tool signature/config mismatch 和 context reduction checkpoint。

本轮 10 个 case 中，`resume_status_accuracy`、`mismatch_detection_rate`、`runtime_identity_detection_rate`、`schema_mismatch_detection_rate`、`checkpoint_event_hit_rate` 都是 100%，`false_accept_count` 和 `false_reject_count` 都是 0。clean resume 正常通过，单文件变化能识别 partial stale，大范围 workspace 变化能识别 mismatch，schema 和 runtime identity 变化也能被识别。限制是它基于当前 metadata 和 workspace hash/fingerprint，不保证所有文件语义一致，也不是分布式任务恢复。

### Q5：Evaluator Regression Benchmark 测什么？

它测 Runtime 行为，不测模型智能。每个 case 有 expected tools、forbidden tools、expected trace events、success keywords、max_steps 和 mock_outputs。Runner 用 FakeModelClient 走完整 Runtime path，然后检查实际工具调用、trace events、失败原因和 step budget。

本轮 13 个 case，`case_pass_rate` 是 84.62%，`expected_trace_event_hit_rate` 是 100%，`step_budget_met_rate` 是 100%。有两个失败 case 被保留：`forbidden_tool_guard_violation` 的原因是 `forbidden tools used: run_shell`，`step_budget_case` 的原因是 `run status was not completed`。这说明 evaluator 可以暴露 failure reasons，而不是只给漂亮 pass rate。它不代表真实模型智能，也不代表真实 coding success rate。

### Q6：这些 benchmark 为什么不是 toy？

因为它不是手动 demo，也不是只跑一个 happy path。它有固定 cases、固定 mock_outputs、可复现脚本 `scripts/run_pure_benchmarks.py`、统一 artifacts、per-case JSONL、`summary.json`、`summary.md` 和失败 case 分析。五个 suite 都可以独立跑，也可以统一跑到 `benchmarks/pure/_runs/latest`。

另外它刻意不依赖真实 LLM 随机性，所以 CI/本地回归更稳定。限制也写进报告：FakeModelClient、offline benchmark、case 数量有限、不代表真实 coding success rate、不代表 SWE-bench、不代表生产级安全。这种诚实边界反而是我想展示的工程能力。

### Q7：这些 benchmark 有什么局限？

主要局限有六个。第一，使用 offline fake model，不代表真实 LLM 行为。第二，case 数量有限。第三，它是 runtime behavior benchmark，不代表真实 coding success rate。第四，没有跑 SWE-bench，不能声称 SWE-bench 成绩。第五，context compression 用字符数 proxy，字符数不等于 token，也不能直接说 token 成本降低。第六，Tool Governance 是本地 policy/path/audit guardrails，不代表生产级安全 sandbox。

所以我会把这些结果定位成“可复现 Runtime 合约验证”，不是模型能力榜单。
