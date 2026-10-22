"""FastAPI app: admin API for the eval runner, plus the portal and AP routers.

Every task run gets its own environment under /e/<env_id>/, created through
POST /admin/envs, so tasks can run concurrently without sharing state.
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field, ValidationError
from starlette.middleware.sessions import SessionMiddleware

from . import ap, data, vendor
from .friction import Difficulty, Friction, resolve
from .state import STORE, get_env, templates

app = FastAPI(title="AgentGate mock portals", docs_url="/admin/docs", redoc_url=None)
app.add_middleware(SessionMiddleware, secret_key="agentgate-mock-portals", session_cookie="ag_session")


class CreateEnvRequest(BaseModel):
    seed: int = 0
    difficulty: Difficulty = "medium"
    friction: dict[str, Any] = Field(default_factory=dict, description="Per-field overrides of the preset")
    env_id: str | None = Field(default=None, pattern=r"^[a-z0-9-]{1,40}$",
                               description="Optional fixed id, so repeated runs see identical URLs")


class CreateEnvResponse(BaseModel):
    env_id: str
    home: str
    friction: Friction


# --- admin API (used by the runner, never by the agent) ---------------------

@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True, "envs": len(STORE)}


@app.post("/admin/envs", response_model=CreateEnvResponse)
def create_env(request: Request, body: CreateEnvRequest | None = None) -> CreateEnvResponse:
    body = body or CreateEnvRequest()
    try:
        friction = resolve(body.difficulty, body.friction)
    except ValidationError as e:
        raise HTTPException(422, f"Invalid friction settings: {e}") from e
    env = STORE.create(body.seed, friction, body.env_id)
    return CreateEnvResponse(env_id=env.id, home=str(request.url_for("home", env_id=env.id)), friction=friction)


@app.get("/admin/envs/{env_id}/state")
def env_state(env_id: str) -> dict:
    return get_env(env_id).to_json()


@app.delete("/admin/envs/{env_id}")
def delete_env(env_id: str) -> dict:
    STORE.delete(env_id)
    return {"ok": True}


# --- pages for humans --------------------------------------------------------

@app.get("/")
def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {})


@app.get("/demo")
def demo(request: Request, seed: int = 1, difficulty: Difficulty = "medium"):
    """Spin up an environment and jump into it, for poking around in a browser."""
    env = STORE.create(seed, resolve(difficulty))
    return RedirectResponse(str(request.url_for("home", env_id=env.id)), status_code=303)


@app.get("/e/{env_id}/", name="home")
def home(request: Request, env_id: str):
    return templates.TemplateResponse(request, "home.html", {"env": get_env(env_id), "vendors": data.VENDORS})


# Order matters: FastAPI matches routes in registration order, so the AP routes
# (/e/{env_id}/ap/...) must come before the catch-all vendor routes (/e/{env_id}/{vendor}/...).
app.include_router(ap.router)
app.include_router(vendor.router)
