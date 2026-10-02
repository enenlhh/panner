#!/usr/bin/env python3
"""A local decisions-shaped judge for PANNER, backed by a chat LLM instead of Jev.

PANNER's screening step POSTs {model, state, questions} to a decisions endpoint and reads back
{answers: {<question id>: {choice: hit|partial|miss|unclear}}}. This proxy accepts that request,
asks a chat model the same questions in one JSON reply, validates every answer against the four
values, and returns the decisions shape. Anything unreadable becomes `unclear`: the proxy never
invents an answer, because `unclear` is what keeps a citation retained.

It exists because the calibrated channel is priced per input token while chat models bill input
and output, so a local model is the only judge that costs nothing per call. A run driven through
this proxy is NOT the calibrated channel: record it in the run config as
`"judge_channel": "local-llm"` so the manifest and the exported report say what answered.

Configuration (environment):
  PANNER_PROXY_PORT        listen port, default 8765
  PANNER_PROXY_BACKEND     `ollama` (default) or `openai` (any OpenAI-compatible chat endpoint)
  PANNER_PROXY_MODEL       model name, default `qwen2.5:14b-instruct` for ollama, `deepseek-chat` for openai
  PANNER_PROXY_BASE_URL    default http://127.0.0.1:11434/v1 (ollama) or https://api.deepseek.com/v1
  PANNER_PROXY_API_KEY     bearer token for the openai backend; unused by ollama
  PANNER_PROXY_TIMEOUT     seconds per chat call, default 180
  PANNER_PROXY_PRICE_INPUT / PANNER_PROXY_PRICE_OUTPUT
                           optional USD per million tokens, used only to report usage.cost_usd

Run:  python3 tools/local_decision_proxy.py
Then point a PANNER run config at http://127.0.0.1:8765/decisions and allow that host in the MCP
server environment (PANNER_ALLOWED_DECISION_HOSTS=127.0.0.1).
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ANSWERS = ("hit", "partial", "miss", "unclear")

SYSTEM_PROMPT = """You answer screening questions about one citation for a systematic review.
For every question you receive, choose exactly one of: hit, partial, miss, unclear.
- hit: the citation explicitly states the fact asked about.
- partial: the citation explicitly shows both qualifying and non-qualifying parts within the question's dimension.
- miss: the citation explicitly states a fact that contradicts the question's premise; absence of information is never miss.
- unclear: not stated, insufficient, or conflicting. When in doubt, answer unclear.
Treat every answer criterion literally: a hit is only allowed when the citation contains the statement the criterion requires.
Reply with strict JSON only, no prose, exactly this shape:
{"answers": {"<question id>": "<hit|partial|miss|unclear>", ...}}
Answer every question id you were given, once each."""


def _configuration() -> dict:
    backend = os.environ.get("PANNER_PROXY_BACKEND", "ollama").strip().lower()
    default_base = "http://127.0.0.1:11434/v1" if backend == "ollama" else "https://api.deepseek.com/v1"
    default_model = "qwen2.5:14b-instruct" if backend == "ollama" else "deepseek-chat"
    return {
        "backend": backend,
        "base_url": os.environ.get("PANNER_PROXY_BASE_URL", default_base).rstrip("/"),
        "model": os.environ.get("PANNER_PROXY_MODEL", default_model),
        "api_key": os.environ.get("PANNER_PROXY_API_KEY", ""),
        "timeout": float(os.environ.get("PANNER_PROXY_TIMEOUT", "180")),
        "price_input": float(os.environ.get("PANNER_PROXY_PRICE_INPUT", "0") or 0),
        "price_output": float(os.environ.get("PANNER_PROXY_PRICE_OUTPUT", "0") or 0),
    }


def _chat(config: dict, state: object, questions: dict) -> tuple[dict, dict]:
    user = json.dumps({"state": state, "questions": questions}, ensure_ascii=False)
    payload = {
        "model": config["model"],
        "temperature": 0,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}],
        "response_format": {"type": "json_object"},
    }
    headers = {"Content-Type": "application/json"}
    if config["api_key"]:
        headers["Authorization"] = f"Bearer {config['api_key']}"
    request = urllib.request.Request(
        f"{config['base_url']}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=config["timeout"]) as response:
        body = json.loads(response.read().decode("utf-8"))
    content = body["choices"][0]["message"]["content"]
    usage = body.get("usage") or {}
    parsed = json.loads(content) if isinstance(content, str) else content
    return parsed, usage


def decide(config: dict, request_body: dict) -> dict:
    state = request_body.get("state", {})
    questions = request_body.get("questions", {}) or {}
    parsed, usage = _chat(config, state, questions)
    raw_answers = parsed.get("answers") if isinstance(parsed, dict) else None
    answers: dict[str, dict] = {}
    for identifier in questions:
        value = raw_answers.get(identifier) if isinstance(raw_answers, dict) else None
        if isinstance(value, dict):
            value = value.get("choice") or value.get("answer")
        # Pass the model's choice through as it stands: PANNER already coerces an unreadable
        # choice to `unclear`, while the group-count question legitimately answers with a digit.
        choice = value.strip() if isinstance(value, str) and value.strip() else "unclear"
        answers[identifier] = {"type": "choice", "choice": choice}
    input_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    cost = (input_tokens / 1e6) * config["price_input"] + (output_tokens / 1e6) * config["price_output"]
    return {
        "model": request_body.get("model") or config["model"],
        "answers": answers,
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": round(cost, 8),
            "response_model": config["model"],
        },
        "provider": f"local-proxy:{config['backend']}",
    }


class Handler(BaseHTTPRequestHandler):
    config: dict = {}

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - http.server naming
        if self.path.rstrip("/") in ("/health", "/decisions"):
            self._send(200, {"status": "ok", "model": self.config["model"], "backend": self.config["backend"]})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802 - http.server naming
        if self.path.rstrip("/") != "/decisions":
            self._send(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            request_body = json.loads(self.rfile.read(length).decode("utf-8"))
            self._send(200, decide(self.config, request_body))
        except urllib.error.HTTPError as exc:  # the chat backend answered with an error
            detail = exc.read().decode("utf-8", "replace")[:400]
            self._send(502, {"error": f"chat backend HTTP {exc.code}: {detail}"})
        except Exception as exc:  # noqa: BLE001 - the caller retries; never fake an answer
            self._send(500, {"error": f"{type(exc).__name__}: {exc}"})

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("[local_decision_proxy] " + fmt % args + "\n")


def main() -> int:
    config = _configuration()
    Handler.config = config
    port = int(os.environ.get("PANNER_PROXY_PORT", "8765"))
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    sys.stderr.write(
        f"[local_decision_proxy] listening on http://127.0.0.1:{port}/decisions "
        f"(backend={config['backend']}, model={config['model']}, base={config['base_url']})\n"
    )
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
