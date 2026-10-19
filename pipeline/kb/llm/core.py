"""Única puerta de entrada a los LLM (AGENTS.md): caché, tope diario, cola y backends.

Los backends son: `llama` (OpenAI-compatible, Qwen local), `claude` (CLI `claude -p`) y
`mock` (respuestas grabadas, para pruebas).
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

import httpx
import yaml

from ..db import ROOT, jdump
from ..util import now, sha256


class LLMError(RuntimeError):
    pass


class UsageCapReached(LLMError):
    pass


@dataclass
class Result:
    data: Any
    engine: str
    model: str
    prompt_version: str
    cost_usd: float = 0.0
    cached: bool = False


def load_config(path: Path | None = None) -> dict:
    return yaml.safe_load((path or ROOT / "config" / "llm.yaml").read_text())


def load_prompt(name: str) -> tuple[str, str]:
    """Devuelve (texto, versión) de prompts/<name>.vN.md; la versión es `vN`."""
    cands = sorted((ROOT / "prompts").glob(f"{name}.v*.md"))
    if not cands:
        raise LLMError(f"no hay prompt para {name}")
    p = cands[-1]
    return p.read_text(), p.stem.split(".")[-1]


class Backend:
    engine = "base"

    def __init__(self, model: str):
        self.model = model

    def call(self, system: str, user: str, schema: dict | None) -> tuple[Any, float]:
        raise NotImplementedError


class LlamaBackend(Backend):
    engine = "local"

    def __init__(self, model: str, base_url: str, timeout: float = 900):
        super().__init__(model)
        self.base_url, self.timeout = base_url.rstrip("/"), timeout

    def call(self, system, user, schema):
        body: dict = {
            "model": self.model, "temperature": 0,
            "chat_template_kwargs": {"enable_thinking": False},  # sin razonamiento en extracción (§8.4)
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        if schema:
            body["response_format"] = {"type": "json_schema",
                                       "json_schema": {"name": "out", "schema": schema, "strict": True}}
        try:
            r = httpx.post(f"{self.base_url}/chat/completions", json=body, timeout=self.timeout)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise LLMError(f"llama-server: {e}") from e
        text = r.json()["choices"][0]["message"]["content"]
        return (json.loads(text) if schema else text), 0.0


class ClaudeCliBackend(Backend):
    """`claude -p` en directorio temporal vacío, con --system-prompt propio y sin herramientas."""
    engine = "claude"

    def __init__(self, model: str, binary: str = "claude", timeout: float = 600):
        super().__init__(model)
        self.binary, self.timeout = binary, timeout

    def call(self, system, user, schema):
        cmd = [self.binary, "-p", "--model", self.model, "--output-format", "json",
               "--system-prompt", system, "--permission-mode", "dontAsk"]
        if schema:
            cmd += ["--json-schema", json.dumps(schema)]
        with tempfile.TemporaryDirectory() as tmp:
            try:
                p = subprocess.run(cmd, input=user, capture_output=True, text=True, cwd=tmp, timeout=self.timeout)
            except subprocess.TimeoutExpired as e:
                raise LLMError("claude -p: timeout") from e
        out = p.stdout.strip()
        try:
            j = json.loads(out)
        except json.JSONDecodeError as e:
            raise LLMError(f"claude -p: salida no JSON ({p.stderr[:200]})") from e
        if j.get("is_error"):
            msg = str(j.get("result", ""))
            if "limit" in msg.lower():
                raise UsageCapReached(msg)
            raise LLMError(msg)
        cost = float(j.get("total_cost_usd") or 0)
        if schema:
            data = j.get("structured_output")
            if data is None:
                data = json.loads(j["result"])
            return data, cost
        return j.get("result", ""), cost


class MockBackend(Backend):
    """Respuestas grabadas: dict {task_key: data} o callable(system, user) -> data."""
    engine = "mock"

    def __init__(self, responses: Callable[[str, str], Any] | dict | None = None, model: str = "mock"):
        super().__init__(model)
        self.responses, self.calls = responses or {}, 0

    def call(self, system, user, schema):
        self.calls += 1
        if callable(self.responses):
            return self.responses(system, user), 0.0
        if user in self.responses:
            return self.responses[user], 0.0
        raise LLMError("MockBackend: sin respuesta grabada")


class LLM:
    def __init__(self, con, backends: dict[str, Backend], config: dict | None = None):
        self.con, self.backends = con, backends
        self.cfg = config or load_config()

    # --- caché y topes
    def _key(self, engine, model, pv, inp):
        return sha256(jdump([engine, model, pv, inp]))

    def _calls_today(self, engine) -> int:
        r = self.con.execute("SELECT n FROM llm_calls WHERE day=? AND engine=?", (now()[:10], engine)).fetchone()
        return r["n"] if r else 0

    def _count(self, engine, cost):
        self.con.execute(
            "INSERT INTO llm_calls(day,engine,n,cost_usd) VALUES(?,?,1,?) "
            "ON CONFLICT(day,engine) DO UPDATE SET n=n+1, cost_usd=cost_usd+excluded.cost_usd",
            (now()[:10], engine, cost))

    def pending(self, task: str, inp: str) -> bool:
        """True si esta entrada costaría una llamada (no está en caché). Para `estimate`/`--dry-run`."""
        engine, backend = self._route(task)
        _, pv = load_prompt(task)
        key = self._key(engine, backend.model, pv, inp)
        return self.con.execute("SELECT 1 FROM llm_cache WHERE key=?", (key,)).fetchone() is None

    def _route(self, task, engine: str | None = None):
        t = self.cfg["tasks"][task]
        engine = engine or t["engine"]
        name = {"local": "local", "claude": "claude", "mock": "mock"}[engine]
        if name not in self.backends and "mock" in self.backends:
            name = "mock"
        backend = self.backends[name]
        model = t.get("model") if t["engine"] == "claude" else t.get("escalate_model")
        if engine == "claude" and model and isinstance(backend, ClaudeCliBackend):
            backend.model = model
        return engine, backend

    def run(self, task: str, user: str, schema: dict | None = None, *, engine: str | None = None,
            retries: int = 3) -> Result:
        t = self.cfg["tasks"][task]
        engine, backend = self._route(task, engine)
        system, pv = load_prompt(task)
        key = self._key(engine, backend.model, pv, user)
        row = self.con.execute("SELECT response FROM llm_cache WHERE key=?", (key,)).fetchone()
        if row:
            return Result(json.loads(row["response"]), engine, backend.model, pv, cached=True)
        if engine == "claude":
            cap = self.cfg["claude"]["daily_call_cap"]
            if self._calls_today("claude") >= cap:
                raise UsageCapReached(f"tope diario de Claude ({cap}) alcanzado")
        delay = 2.0
        for attempt in range(retries):
            try:
                data, cost = backend.call(system, user, schema)
                break
            except UsageCapReached:
                raise
            except LLMError:
                if attempt == retries - 1:
                    raise
                time.sleep(delay)
                delay *= 2
        self._count(engine, cost)
        self.con.execute("INSERT OR REPLACE INTO llm_cache VALUES(?,?,?)", (key, json.dumps(data, ensure_ascii=False), now()))
        self.con.commit()
        return Result(data, engine, backend.model, pv, cost)


def default_llm(con) -> LLM:
    cfg = load_config()
    b: dict[str, Backend] = {
        "local": LlamaBackend(cfg["local"]["model"], cfg["local"]["base_url"]),
        "claude": ClaudeCliBackend("sonnet"),
    }
    return LLM(con, b, cfg)
