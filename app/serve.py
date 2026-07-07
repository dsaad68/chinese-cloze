#!/usr/bin/env python3
"""Local dev server for the PWA — WITH HTTP Range support.

Use this instead of ``python3 -m http.server`` when testing locally, because the
read-along audio (``<audio>`` elements) needs byte-range requests to stream.
``http.server`` answers a ``Range`` request with a full ``200`` and no
``Accept-Ranges``, which makes Chrome's media element stall. GitHub Pages (the
deploy target) supports Range natively, so this only matters for local testing.

    python3 app/serve.py            # serves app/ on http://localhost:8899
    python3 app/serve.py 8080       # custom port
"""
from __future__ import annotations
import os
import re
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer


class RangeHandler(SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):  # noqa: N802 (stdlib naming)
        rng = self.headers.get("Range")
        path = self.translate_path(self.path)
        m = re.match(r"bytes=(\d*)-(\d*)$", rng or "")
        if not rng or not m or not os.path.isfile(path):
            return super().do_GET()  # normal full response
        size = os.path.getsize(path)
        start = int(m.group(1)) if m.group(1) else 0
        end = int(m.group(2)) if m.group(2) else size - 1
        end = min(end, size - 1)
        if start > end:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.end_headers()
            return
        length = end - start + 1
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(length))
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            self.wfile.write(f.read(length))


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8899
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    print(f"Serving app/ (with Range support) at http://localhost:{port}/")
    ThreadingHTTPServer(("", port), RangeHandler).serve_forever()


if __name__ == "__main__":
    main()
