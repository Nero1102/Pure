# Pure Architecture

Pure is an Agent Runtime project with enterprise-adjacent design. It is not described as a production-grade distributed system. The current system keeps the proven `PureRuntime.ask()` runtime loop and adds platform capabilities around it: FastAPI, metadata persistence, ToolGateway governance, checkpoints, knowledge retrieval, evaluator reports, and Docker packaging.

## Component Map

```text
CLI / FastAPI
  -> RuntimeService
  -> PureRuntime.ask()
  -> PromptService + ContextManager
  -> KnowledgeService
  -> ModelClient
  -> ToolExecutionService
  -> ToolGateway
  -> RunStore / SessionStore
  -> SQLAlchemy metadata repositories
```

The Runtime remains the owner of task execution. The API, database, Evaluator, and Docker layers are adapters around it.

## Why Trace Uses JSONL

Trace is an event stream. JSONL lets Pure append one event at a time without rewriting a large JSON document. It is easy to inspect with standard tools, works well for partially completed or failed runs, and lets tests read only the evidence they need.

## Why Trace and Report Are Not Stored Directly In The Database

The database stores platform metadata: projects, tasks, runs, tool-call summaries, checkpoint indexes, and artifact paths. Full trace and report payloads stay under `.pure/runs/<run_id>/` because they can grow, they are the audit artifacts used by CLI and API paths, and duplicating them into the database would create two sources of truth.

## Why Knowledge Is Only Context Augmentation

Knowledge is not a separate support chatbot or RAG product. It loads project documents, chunks them, creates deterministic fake embeddings by default, and injects selected snippets into the prompt as `knowledge_context`. The model still answers through the Runtime; Knowledge only improves context.

## Why ToolGateway Is Independent

ToolGateway separates model intent from side effects. The model asks for a tool, the Runtime forwards the request, and ToolGateway applies policy, risk metadata, approval mode, latency tracking, and audit fields before calling the restricted tool runner.

## Why Runtime Is Not Rewritten

`PureRuntime.ask()` already owns prompt building, parsing, tool execution, checkpointing, memory, trace, and reports. Rewriting it would risk changing established behavior. The current design adds API, database, evaluator, and deployment surfaces by delegating into the existing loop.

## Current Limits

Background jobs are process-local. Cancellation is cooperative. Database migrations are not formalized yet. Redis, Celery, Kafka, and WebSocket streaming are intentionally absent.
