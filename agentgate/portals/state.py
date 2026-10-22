"""Shared state for the portal routers: the environment store and templates."""
from __future__ import annotations

import threading
import uuid
from pathlib import Path

from fastapi import HTTPException
from fastapi.templating import Jinja2Templates

from .data import Env, generate_env
from .friction import Friction

templates = Jinja2Templates(directory=Path(__file__).with_name("templates"))


class EnvStore:
    """In-memory environments, one per task run. The lock guards concurrent runs."""

    def __init__(self) -> None:
        self._envs: dict[str, Env] = {}
        self._lock = threading.Lock()

    def create(self, seed: int, friction: Friction) -> Env:
        env = generate_env(uuid.uuid4().hex[:10], seed, friction)
        with self._lock:
            self._envs[env.id] = env
        return env

    def get(self, env_id: str) -> Env:
        env = self._envs.get(env_id)
        if env is None:
            raise HTTPException(404, "Unknown environment")
        return env

    def delete(self, env_id: str) -> None:
        with self._lock:
            self._envs.pop(env_id, None)

    def __len__(self) -> int:
        return len(self._envs)


STORE = EnvStore()
LOCK = threading.Lock()  # guards writes into an env (e.g. AP entries)


def get_env(env_id: str) -> Env:
    """FastAPI dependency: resolves the {env_id} path parameter to an Env or 404s."""
    return STORE.get(env_id)
