#!/usr/bin/env python3
"""An offline stand-in for a chat backend, used to test the local judge without any API cost.

It speaks the OpenAI-compatible `POST /v1/chat/completions` shape and answers the questions the
proxy sent it with scripted choices, so the whole PANNER path (screen preview -> confirm ->
judgments -> export) can be exercised end to end with no network and no spending.

Configuration (environment):
  PANNER_STUB_PORT     listen port, default 8899
  PANNER_STUB_ANSWERS  path to a JSON file: {"<substring of the citation title>": {"<signal id>": "<hit|partial|miss|unclear>"}}
                       The first matching substring wins; unmatched citations answer `unclear`.

Run:  python3 tools/stub_chat_server.py
Then start local_decision_proxy.py with
  PANNER_PROXY_BACKEND=openai PANNER_PROXY_BASE_URL=http://127.0.0.1:8899/v1
"""

from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ANSWERS = ("hit", "partial", "miss", "unclear")


def _scripted() -> dict:
    path = os.environ.get("PANNER_STUB_ANSWERS", "")
    if not path:
        return {}
    with open(path, encoding="utf-8") as stream:
        return json.load(stream)


class Handler(BaseHTTPRequestHandler):
    scripted: dict = {}

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802 - http.server naming
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self._send(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        user = json.loads(body["messages"][-1]["content"])
        state = user.get("state", {})
        title = state.get("title", "") if isinstance(state, dict) else str(state)
        questions = user.get("questions", {}) or {}
        scripted: dict = {}
        for needle, answers in self.scripted.items():
            if needle in title:
                scripted = answers
                break
        answers = {identifier: scripted.get(identifier, "unclear") for identifier in questions}
        content = json.dumps({"answers": answers}, ensure_ascii=False)
        self._send(200, {
            "choices": [{"message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 100},
            "model": body.get("model", "stub"),
        })

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("[stub_chat_server] " + fmt % args + "\n")


def main() -> int:
    Handler.scripted = _scripted()
    port = int(os.environ.get("PANNER_STUB_PORT", "8899"))
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    sys.stderr.write(f"[stub_chat_server] listening on http://127.0.0.1:{port}/v1/chat/completions with {len(Handler.scripted)} scripted titles\n")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
