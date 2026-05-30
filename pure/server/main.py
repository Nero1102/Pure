from contextlib import asynccontextmanager

from fastapi import FastAPI

from pure.server.api import evals, knowledge, projects, runs, sessions, tasks, tools
from pure.utils.config import load_project_env


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_project_env(".")
    yield


app = FastAPI(title="Pure Runtime API", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok"}


app.include_router(sessions.router)
app.include_router(projects.router)
app.include_router(tasks.router)
app.include_router(runs.router)
app.include_router(tools.router)
app.include_router(knowledge.router)
app.include_router(evals.router)
