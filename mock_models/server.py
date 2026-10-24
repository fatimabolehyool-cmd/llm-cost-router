"""Mock model server for tests and feasibility checks. Not used in production.

Serves both protocols the router speaks:
  POST /api/chat            Ollama-compatible (the juniors), keyed by model name
  POST /senior/v1/chat      the provider-neutral senior protocol
Records every payload it receives, so tests can prove what did (and did not) leave the router.

Behaviour per model is set with POST /control, e.g. {"junior-a": "answer:B", "senior": "error"}:
  answer:<text>   reply with <text>
  slow:<s>:<text> wait <s> seconds, then reply with <text> (load and overload tests)
  echo            reply with the user's own prompt (a model repeating personal data back)
  error           HTTP 500
  timeout         sleep 60 s (longer than any router timeout)
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

RECORDS = Path(os.environ.get("MOCK_RECORDS_DIR", "/tmp/mock-records"))
RECORDS.mkdir(parents=True, exist_ok=True)
DEFAULT = {"junior-a": "answer:A", "junior-b": "answer:A", "senior": "answer:A"}
state: dict[str, str] = dict(DEFAULT)

app = FastAPI(title="mock models")


def _record(kind: str, payload: dict) -> None:
    with (RECORDS / f"{kind}.jsonl").open("a") as f:
        f.write(json.dumps(payload) + "\n")


async def _behave(name: str, user_text: str) -> str | JSONResponse:
    b = state.get(name, "answer:A")
    if b.startswith("slow:"):            # slow:<seconds>:<answer>  e.g. slow:2:A
        _, secs, ans = b.split(":", 2)
        await asyncio.sleep(float(secs))
        return ans
    if b == "timeout":
        await asyncio.sleep(60)
    if b == "error":
        return JSONResponse(status_code=500, content={"error": "mock failure"})
    if b == "echo":
        return user_text
    return b.split(":", 1)[1] if b.startswith("answer:") else ""


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/control")
async def control(request: Request):
    state.update(await request.json())
    return state


@app.post("/control/reset")
async def reset():
    state.clear()
    state.update(DEFAULT)
    for f in RECORDS.glob("*.jsonl"):
        f.unlink()
    return {"reset": True}


@app.get("/records/{kind}")
async def records(kind: str):
    f = RECORDS / f"{kind}.jsonl"
    return {"lines": f.read_text().splitlines() if f.exists() else []}


@app.post("/api/chat")
async def ollama_chat(request: Request):
    body = await request.json()
    _record("juniors", body)
    user = next((m["content"] for m in body.get("messages", []) if m["role"] == "user"), "")
    out = await _behave(body.get("model", ""), user)
    if isinstance(out, JSONResponse):
        return out
    return {"model": body.get("model"), "message": {"role": "assistant", "content": out},
            "done": True, "prompt_eval_count": len(user) // 4, "eval_count": 2,
            "total_duration": 1_000_000, "load_duration": 0}


@app.post("/senior/v1/chat")
async def senior_chat(request: Request):
    body = await request.json()
    _record("senior", body)
    out = await _behave("senior", body.get("user", ""))
    if isinstance(out, JSONResponse):
        return out
    return {"text": out, "input_tokens": len(body.get("user", "")) // 4, "output_tokens": 2}
