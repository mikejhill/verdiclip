"""Serve only an authored synthetic fixture and receive its bounded telemetry."""

from __future__ import annotations

import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import cast, override

MAX_STATE_BYTES = 16_384
MAX_STATES = 256


class FixtureServer:
    """Local fixture page, with no browser injection or remote content."""

    def __init__(self, source: Path, output: Path) -> None:
        self.states: list[dict[str, object]] = []
        self.output = output
        output.mkdir()
        self.token = secrets.token_hex(16)
        self.html = self._page(source).encode()
        self.server = HTTPServer(("127.0.0.1", 0), self._handler())
        self.server.timeout = 0.2
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @staticmethod
    def _page(source: Path) -> str:
        rows = "".join(
            '<div class="row" style="background:rgb('
            f"{32 + index * 53 % 190},{32 + index * 29 % 190},"
            f'{32 + index * 71 % 190})">Owned row {index:02}</div>'
            for index in range(47)
        )
        return source.read_text(encoding="utf-8").replace("ROWS", rows)

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        owner = self

        class Handler(BaseHTTPRequestHandler):
            # Bound request reads, including idle browser preconnects.
            timeout = 1.0

            def do_GET(self) -> None:
                if self.path != f"/{owner.token}/":
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(owner.html)))
                self.end_headers()
                self.wfile.write(owner.html)

            def do_POST(self) -> None:
                size = int(self.headers.get("Content-Length", "0"))
                if (
                    self.path != f"/{owner.token}/state"
                    or not 0 < size <= MAX_STATE_BYTES
                    or len(owner.states) >= MAX_STATES
                ):
                    self.send_error(400)
                    return
                value = json.loads(self.rfile.read(size))
                if not isinstance(value, dict):
                    self.send_error(400)
                    return
                state = cast("dict[str, object]", value)
                path = owner.output / f"{len(owner.states):04}.json"
                pending = path.with_suffix(".tmp")
                pending.write_text(json.dumps(state, indent=2), encoding="utf-8")
                pending.replace(path)
                owner.states.append(state)
                self.send_response(204)
                self.end_headers()

            @override
            def log_message(self, format: str, *args: object) -> None:
                pass

        return Handler

    @property
    def url(self) -> str:
        """Return this one owned loopback page's URL."""
        return f"http://127.0.0.1:{self.server.server_port}/{self.token}/"

    def close(self) -> None:
        """Stop the server thread without an unbounded join."""
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        if self.thread.is_alive():
            raise RuntimeError("Owned fixture server did not stop")
