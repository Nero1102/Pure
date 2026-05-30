# API

Start the API:

```bash
uvicorn pure.server.main:app --reload
```

Initialize the default SQLite metadata database:

```bash
python -m pure.db.init_db
```

## Core Endpoints

- `GET /health`
- `POST /projects`
- `GET /projects/{project_id}`
- `POST /tasks`
- `POST /tasks/{task_id}/run`
- `GET /tasks/{task_id}/status`
- `POST /tasks/{task_id}/cancel`
- `GET /tasks/{task_id}/checkpoints`
- `POST /tasks/{task_id}/resume`
- `GET /runs/{run_id}`
- `GET /runs/{run_id}/trace`
- `GET /runs/{run_id}/report`
- `GET /tools`
- `POST /knowledge/documents`
- `POST /knowledge/index`
- `POST /knowledge/search`
- `POST /eval/run`
- `GET /eval/{eval_id}/report`

## Dry Run Task Example

```bash
curl -X POST http://localhost:8000/projects \
  -H "Content-Type: application/json" \
  -d '{"name":"Pure","root_path":"."}'
```

Use the returned project id:

```bash
curl -X POST http://localhost:8000/tasks \
  -H "Content-Type: application/json" \
  -d '{"project_id":"project_id_here","title":"Dry run","prompt":"Inspect without a real model.","dry_run":true}'
```

Run the task:

```bash
curl -X POST http://localhost:8000/tasks/task_id_here/run \
  -H "Content-Type: application/json" \
  -d '{"dry_run":true}'
```
