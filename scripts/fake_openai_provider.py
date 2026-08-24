from __future__ import annotations

import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

MODEL_IDS = (
    "devflow-e2e-planner",
    "devflow-e2e-developer",
    "devflow-e2e-reviewer",
    "devflow-e2e-repair",
)

PLANNER_MARKER = "DevFlow Multi-Agent Planner"
DEVELOPER_MARKER = "DevFlow Developer Agent"
REVIEWER_MARKER = "DevFlow Independent Reviewer Agent"
REPAIR_MARKER = "DevFlow Repair Agent"


class RequestRecorder:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text("", encoding="utf-8")

    def record(self, payload: dict[str, Any]) -> None:
        line = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        with self._lock:
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")


class FakeOpenAIHandler(BaseHTTPRequestHandler):
    recorder: RequestRecorder

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/healthz":
            self._json_response(200, {"status": "ok"})
            return
        if self.path == "/v1/models":
            self.recorder.record({"kind": "models"})
            self._json_response(
                200,
                {
                    "object": "list",
                    "data": [
                        {
                            "id": model_id,
                            "object": "model",
                            "created": 0,
                            "owned_by": "devflow-v2.5-e2e",
                        }
                        for model_id in MODEL_IDS
                    ],
                },
            )
            return
        self._json_response(404, {"error": {"message": "not found"}})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/v1/chat/completions":
            self._json_response(404, {"error": {"message": "not found"}})
            return
        try:
            content_length = int(self.headers.get("content-length", "0"))
            payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
        except (ValueError, json.JSONDecodeError):
            self._json_response(400, {"error": {"message": "invalid JSON"}})
            return

        model = str(payload.get("model", ""))
        messages = payload.get("messages")
        if not isinstance(messages, list):
            self._json_response(400, {"error": {"message": "messages must be a list"}})
            return

        system_text = "\n".join(
            str(message.get("content") or "")
            for message in messages
            if isinstance(message, dict) and message.get("role") == "system"
        )
        agent = self._agent_from_system(system_text)
        self.recorder.record(
            {
                "kind": "chat.completions",
                "agent": agent,
                "model": model,
                "message_roles": [
                    message.get("role")
                    for message in messages
                    if isinstance(message, dict)
                ],
                "tools": [
                    tool.get("function", {}).get("name")
                    for tool in payload.get("tools", [])
                    if isinstance(tool, dict)
                ],
            }
        )

        if agent == "planner":
            self._completion(model, content=self._planner_content())
            return
        if agent == "developer":
            if any(
                isinstance(message, dict) and message.get("role") == "tool"
                for message in messages
            ):
                self._completion(model, content="Implementation complete.")
                return
            self._completion(
                model,
                content=None,
                tool_calls=[
                    {
                        "id": "call-v25-write",
                        "type": "function",
                        "function": {
                            "name": "write_file",
                            "arguments": json.dumps(
                                {
                                    "path": "distributed_e2e.txt",
                                    "content": "DevFlow V2.5 distributed E2E\n",
                                },
                                separators=(",", ":"),
                            ),
                        },
                    }
                ],
            )
            return
        if agent == "reviewer":
            self._completion(
                model,
                content=json.dumps(
                    {
                        "decision": "PASS",
                        "summary": "The bounded distributed E2E change satisfies the task.",
                        "issues": [],
                    },
                    separators=(",", ":"),
                ),
            )
            return
        if agent == "repair":
            self._json_response(
                500,
                {"error": {"message": "repair must not be invoked by the V2.5 happy path"}},
            )
            return

        self._json_response(
            400,
            {"error": {"message": "unrecognized DevFlow agent system prompt"}},
        )

    @staticmethod
    def _agent_from_system(system_text: str) -> str:
        if PLANNER_MARKER in system_text:
            return "planner"
        if DEVELOPER_MARKER in system_text:
            return "developer"
        if REVIEWER_MARKER in system_text:
            return "reviewer"
        if REPAIR_MARKER in system_text:
            return "repair"
        return "unknown"

    @staticmethod
    def _planner_content() -> str:
        return json.dumps(
            {
                "tasks": [
                    {
                        "task": {
                            "task_id": "distributed-e2e",
                            "objective": (
                                "Create distributed_e2e.txt containing the V2.5 distributed "
                                "release marker."
                            ),
                            "readable_files": ["README.md"],
                            "writable_files": ["distributed_e2e.txt"],
                            "readonly_files": [],
                            "acceptance_criteria": [
                                "distributed_e2e.txt contains the DevFlow V2.5 distributed E2E marker"
                            ],
                            "verification_commands": [
                                "python -c \"from pathlib import Path; assert "
                                "Path('distributed_e2e.txt').read_text().strip() == "
                                "'DevFlow V2.5 distributed E2E'\""
                            ],
                            "max_retries": 0,
                        },
                        "depends_on": [],
                    }
                ]
            },
            separators=(",", ":"),
        )

    def _completion(
        self,
        model: str,
        *,
        content: str | None,
        tool_calls: list[dict[str, Any]] | None = None,
    ) -> None:
        message: dict[str, Any] = {"role": "assistant", "content": content}
        if tool_calls:
            message["tool_calls"] = tool_calls
        self._json_response(
            200,
            {
                "id": f"chatcmpl-devflow-{time.time_ns()}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": message,
                        "finish_reason": "tool_calls" if tool_calls else "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 11,
                    "completion_tokens": 7,
                    "total_tokens": 18,
                },
            },
        )

    def _json_response(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Deterministic OpenAI-compatible fixture for DevFlow V2.5 release E2E."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18080)
    parser.add_argument("--log", type=Path, required=True)
    args = parser.parse_args()

    FakeOpenAIHandler.recorder = RequestRecorder(args.log.resolve())
    server = ThreadingHTTPServer((args.host, args.port), FakeOpenAIHandler)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
