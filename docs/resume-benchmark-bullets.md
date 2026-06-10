# Pure Resume Benchmark Bullets

Source artifacts:

- `docs/benchmarks/pure-benchmark-results.md`
- `benchmarks/pure/_runs/latest/summary.json`
- `benchmarks/pure/_runs/latest/summary.md`
- `benchmarks/pure/_runs/latest/<suite>/results.json`
- `benchmarks/pure/_runs/latest/<suite>/per_case_results.jsonl`

All statements below are based on the latest local FakeModelClient/mock-output benchmark run. They are runtime behavior results, not real-model success rates.

## 中文简历 bullet 候选

按表述风险从低到高排序：

1. 为 Pure Agent Runtime 搭建离线 Benchmark Suite，覆盖 context compression、tool loop、tool governance、checkpoint/resume 和 evaluator regression。
2. 实现统一 benchmark runner，生成 `summary.json`、per-case JSONL 和 Markdown 报告，支持五个 suite 的可复现本地回归运行。
3. 基于 FakeModelClient/mock outputs 构建 86 个 offline case/variant rows，验证 runtime behavior，而不依赖真实 LLM 随机性。
4. 为 ToolGateway 增加治理 benchmark，15 个离线 case 中 policy pass、unsafe rejection、workspace escape block 和 risky audit coverage 均为 100%。
5. 为 checkpoint/resume 增加恢复状态 benchmark，10 个 case 覆盖 clean resume、partial stale、workspace mismatch、schema/runtime identity mismatch。
6. 为 Tool Repetition Guard 增加 off/warn/block 对照 benchmark，block 模式将重复执行调用从 10 降到 0，并记录 repeated/rejected trace events。
7. 为 context compression 建立字符级 benchmark，平均 prompt chars 从 7162.33 降到 2339.08，verifier/current request/final status 均为 100%。
8. 将 evaluator regression 固化为 13 个离线 case，case pass rate 为 84.62%，并保留 forbidden tool 与 step budget 失败原因分析。

## 英文简历 bullet 候选

Ordered from lower to higher wording risk:

1. Built an offline benchmark suite for Pure Agent Runtime covering context compression, tool-loop control, tool governance, checkpoint/resume, and evaluator regression.
2. Implemented a unified benchmark runner that emits `summary.json`, per-case JSONL, and Markdown reports for reproducible local regression runs.
3. Created 86 offline case/variant rows with FakeModelClient/mock outputs to validate runtime behavior without real-LLM variability.
4. Benchmarked ToolGateway governance across 15 offline cases, measuring policy pass, unsafe rejection, workspace escape blocking, and risky-tool audit coverage.
5. Added checkpoint/resume benchmarks covering clean resume, partial stale, workspace mismatch, schema mismatch, and runtime identity mismatch across 10 cases.
6. Benchmarked Tool Repetition Guard with guard off/warn/block modes; block mode reduced repeated executed calls from 10 to 0 and emitted rejection trace events.
7. Built a character-level context compression benchmark; latest run reduced average prompt chars from 7162.33 to 2339.08 with verifier/request/final checks passing.
8. Converted evaluator behavior into 13 offline regression cases with trace-event, tool-policy, step-budget, and failure-reason reporting.

## 最推荐写法

1. 为 Pure Agent Runtime 搭建可复现离线 Benchmark Suite，覆盖 context compression、tool loop、ToolGateway governance、checkpoint/resume 和 evaluator regression，并生成 JSON/JSONL/Markdown artifacts。
2. 基于 FakeModelClient/mock outputs 构建 86 个 offline case/variant rows，验证 runtime behavior 与 trace/report contract，避免将结果混同为真实模型能力。
3. 为 ToolGateway 和 Checkpoint/Resume 增加专项 benchmark：15 个治理 case 覆盖 readonly/path escape/risky audit，10 个恢复 case 覆盖 stale/mismatch/schema/runtime identity。

## 不建议写法

- 不写“token 成本降低 65.79%”：当前只测了字符数，没有测 tokenizer token。
- 不写“Agent 成功率提升 XX%”：没有跑真实任务级 coding benchmark。
- 不写“SWE-bench pass rate”：Pure 没有运行 SWE-bench。
- 不写“真实模型成功率 84.62%”：Evaluator Regression 使用 FakeModelClient/mock outputs。
- 不写“生产级安全”：Tool Governance 只是本地 ToolGateway policy、path boundary 和 audit diff。
- 不写“解决所有 Agent loop”：Repetition Guard 只测短窗口同工具同归一化参数重复调用。
- 不写“压缩率提升 XX%”而不带 verifier/current-request 限制：压缩率必须和保真检查一起讲。
- 不写“外部项目数据证明 Pure”：不能引用 Pico 或其他项目作者数据作为 Pure 数据。

## 面试解释话术

### Q1：你的压缩率怎么测的？

我把同一批 benchmark case 分成 baseline 和 reduced 两种 context 配置跑。baseline 保留更多上下文，reduced 开启上下文治理，然后记录 Runtime 实际构造出来的 prompt 字符数。公式是：

```text
compression_rate = (baseline_prompt_chars - reduced_prompt_chars) / baseline_prompt_chars
```

这次本地结果是 12 个 case，平均 baseline prompt 7162.33 chars，平均 reduced prompt 2339.08 chars，平均字符压缩率 65.79%。我用 chars 是因为不同模型 tokenizer 不一样，字符数是跨模型 proxy；它不等于 token，也不能直接说 token 成本降低。

我也不会只看压缩率。压缩如果删掉 current request 就没有意义，所以报告同时看 verifier pass rate、current request preserved rate 和 normal final rate，这三项本轮都是 100%。结果文件在 `benchmarks/pure/_runs/latest/context_compression/results.json`，汇总在 `docs/benchmarks/pure-benchmark-results.md`。当前限制是 case 数量有限、使用 FakeModelClient，而且有一个 negative compression case 被保留，所以只能说这是 runtime context behavior benchmark。

### Q2：Tool Repetition Guard 怎么证明有效？

我做的是 off / warn / block 三组对照，所有 case 使用同一批 mock outputs，只改 guard 配置。guard off 下 repeated_tool_call_count 是 10；warn 下 repeated_tool_call_count 仍是 10，但记录了 10 个 `repeated_tool_call_detected` event；block 下 repeated executed calls 降到 0，并记录 10 个 `tool_rejected_repeated_call` event。

检测方式是同工具名 + normalized args 的短窗口比较。路径会归一化，比如 `pure`、`.\pure`、`pure/` 会被看成同一逻辑路径，同时忽略 timeout、limit、max_lines 这类非语义参数。warn 只告警不阻断，block 会拒绝重复工具执行。本轮 block 的 step_saving_estimate 是 0，因为固定 mock sequence 的步数形态没有变化，所以我只说重复执行被阻断，不说节省了 step。它不能解决所有 Agent loop，也不能替代 planner。

### Q3：Tool Governance Benchmark 测什么？

它测 ToolGateway 作为模型和本地 workspace 之间的治理边界。case 包括 readonly 下允许 read/list/search，拒绝 write/patch/shell；拒绝 parent path escape 和 absolute path escape；拒绝 invalid tool name、missing arg、wrong arg type；并验证 risky write/patch 是否记录 workspace diff。

本轮 15 个 offline case 里，policy_pass_rate、unsafe_rejection_rate、safe_allow_rate、workspace_escape_block_rate、readonly_block_rate、risky_tool_audit_coverage 都是 100%，false_allow 和 false_reject 都是 0。risky tool audit 会检查 `workspace_changed`、`affected_paths` 等字段。这不是生产级 sandbox，也不能防所有命令风险，只能说当前 ToolGateway 的本地策略和 audit contract 被 benchmark 覆盖到了。

### Q4：Checkpoint Resume Benchmark 测什么？

它测的是恢复状态识别，不是分布式事务恢复。case 覆盖 clean resume、partial stale、workspace mismatch、schema mismatch、runtime identity mismatch、tool signature/config mismatch 和 context reduction checkpoint。

本轮 10 个 case 中，resume_status_accuracy、mismatch_detection_rate、runtime_identity_detection_rate、schema_mismatch_detection_rate、checkpoint_event_hit_rate 都是 100%，false_accept_count 和 false_reject_count 都是 0。clean resume 正常通过，单文件变化能识别 partial stale，大范围 workspace 变化能识别 mismatch，schema 和 runtime identity 变化也能被识别。限制是它基于当前 metadata 和 workspace hash/fingerprint，不保证所有文件语义一致，也不是分布式任务恢复。

### Q5：Evaluator Regression Benchmark 测什么？

它测 Runtime 行为，不测模型智能。每个 case 有 expected_tools、forbidden_tools、expected_trace_events、success_keywords、max_steps 和 mock_outputs。Runner 用 FakeModelClient 走完整 Runtime path，然后检查实际工具调用、trace events、失败原因和 step budget。

本轮 13 个 case，case_pass_rate 是 84.62%，expected_trace_event_hit_rate 是 100%，step_budget_met_rate 是 100%。有两个失败 case 被保留：`forbidden_tool_guard_violation` 的原因是 `forbidden tools used: run_shell`，`step_budget_case` 的原因是 `run status was not completed`。这说明 evaluator 可以暴露 failure reasons，而不是只给漂亮 pass rate。它不代表真实模型智能，也不代表真实 coding success rate。

### Q6：这些 benchmark 为什么不是 toy？

因为它不是手动 demo，也不是只跑一个 happy path。它有固定 cases、固定 mock_outputs、可复现脚本 `scripts/run_pure_benchmarks.py`、统一 artifacts、per-case JSONL、summary.json、summary.md 和失败 case 分析。五个 suite 都可以独立跑，也可以统一跑到 `benchmarks/pure/_runs/latest`。

另外它刻意不依赖真实 LLM 随机性，所以 CI/本地回归更稳定。限制也写进报告：FakeModelClient、offline benchmark、case 数量有限、不代表真实 coding success rate、不代表 SWE-bench、不代表生产级安全。这种诚实边界反而是我想展示的工程能力。

### Q7：这些 benchmark 有什么局限？

主要局限有六个。第一，使用 offline fake model，不代表真实 LLM 行为。第二，case 数量有限。第三，它是 runtime behavior benchmark，不代表真实 coding success rate。第四，没有跑 SWE-bench，不能声称 SWE-bench 成绩。第五，context compression 用字符数 proxy，字符数不等于 token，也不能直接说 token 成本降低。第六，Tool Governance 是本地 policy/path/audit guardrails，不代表生产级安全 sandbox。

所以我会把这些结果定位成“可复现 Runtime 合约验证”，不是模型能力榜单。
