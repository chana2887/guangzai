"""Serve the Guangzai control panel and projection from the local device."""

import argparse
import json
import os
import re
import sys
import webbrowser
from contextlib import suppress
from functools import partial
from getpass import getpass
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from companion import ACTIONS, FOCUS_MINUTES, Companion
from conversation import ChatProvider
from hardware import HardwareBridge

ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT / "public"


class Handler(SimpleHTTPRequestHandler):
    extensions_map = {
        **SimpleHTTPRequestHandler.extensions_map,
        ".js": "application/javascript",
        ".json": "application/json",
        ".mp4": "video/mp4",
        ".webm": "video/webm",
    }

    def __init__(self, *args, companion, port, **kwargs):
        self.companion = companion
        self.hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        super().__init__(*args, directory=str(PUBLIC), **kwargs)

    def log_request(self, code="-", size="-"):
        if str(code) not in {"200", "206", "304"}:
            super().log_request(code, size)

    def json_response(self, status, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def local_origin(self):
        origin = self.headers.get("Origin")
        host = self.headers.get("Host")
        return host in self.hosts and origin in (None, f"http://{host}")

    def send_head(self):
        if not self.local_origin():
            self.send_error(403)
            return None
        try:
            path = Path(self.translate_path(self.path)).resolve()
            relative = path.relative_to(PUBLIC)
        except (ValueError, OSError):
            self.send_error(404)
            return None
        if any(part.startswith(".") for part in relative.parts) or (
            path.is_dir() and path != PUBLIC
        ):
            self.send_error(404)
            return None
        byte_range = self.headers.get("Range")
        if byte_range and path.is_file() and path.suffix in {".mp4", ".webm"}:
            return self.video_range(path, byte_range)
        return super().send_head()

    def video_range(self, path, byte_range):
        size = path.stat().st_size
        match = re.fullmatch(r"bytes=(\d{0,16})-(\d{0,16})", byte_range)
        if match and any(match.groups()):
            first, last = match.groups()
            start = int(first) if first else max(0, size - int(last))
            end = min(size - 1, int(last)) if first and last else size - 1
            if start <= end:
                try:
                    stream = path.open("rb")
                    stream.seek(start)
                except OSError:
                    self.send_error(404)
                    return None
                self.range_length = end - start + 1
                self.send_response(206)
                self.send_header("Content-Type", self.guess_type(str(path)))
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(self.range_length))
                self.end_headers()
                return stream
        self.send_response(416)
        self.send_header("Content-Range", f"bytes */{size}")
        self.send_header("Content-Length", "0")
        self.end_headers()
        return None

    def copyfile(self, source, outputfile):
        # A browser switching clips can close an in-flight media range request.
        with suppress(ConnectionError):
            remaining = getattr(self, "range_length", None)
            if remaining is None:
                return super().copyfile(source, outputfile)
            while remaining:
                data = source.read(min(64 * 1024, remaining))
                if not data:
                    break
                outputfile.write(data)
                remaining -= len(data)

    def do_GET(self):
        if not self.local_origin():
            return self.send_error(403)
        url = urlsplit(self.path)
        if url.path == "/api/health":
            return self.json_response(200, {"ok": True})
        if url.path == "/api/state":
            return self.json_response(200, self.companion.state())
        if url.path == "/api/history":
            params = parse_qs(url.query)
            collection = params.get("type", ["events"])[0]
            if collection not in {"facts", "events", "diary"}:
                return self.json_response(400, {"error": "未知记录类型"})
            return self.json_response(200, {
                "items": self.companion.history(
                    collection, params.get("q", [""])[0][:100], params.get("past") == ["1"],
                ),
            })
        if url.path == "/api/export":
            return self.json_response(200, self.companion.export())
        if url.path.startswith("/api/"):
            return self.json_response(404, {"error": "接口不存在"})
        return super().do_GET()

    def do_POST(self):
        if not self.local_origin():
            return self.send_error(403)
        path = urlsplit(self.path).path
        if path not in {"/api/interact", "/api/chat", "/api/gesture", "/api/button"}:
            return self.json_response(404, {"error": "接口不存在"})
        if self.headers.get_content_type() != "application/json":
            return self.json_response(415, {"error": "需要 JSON 数据"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 4096:
                raise ValueError
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError
        except (ValueError, TypeError):
            return self.json_response(400, {"error": "无效的请求"})
        if path == "/api/button":
            key, revision = payload.get("key"), payload.get("revision")
            if key not in ("left", "right", "center") or type(revision) is not int or revision < 0:
                return self.json_response(400, {"error": "按键或选项版本无效"})
            return self.json_response(200, self.companion.button(key, revision))
        if path == "/api/interact":
            action, minutes = payload.get("action"), payload.get("minutes", 25)
            if (not isinstance(action, str) or action not in ACTIONS or
                    type(minutes) is not int or minutes not in FOCUS_MINUTES):
                return self.json_response(400, {"error": "未知动作或时长"})
            return self.json_response(200, self.companion.interact(action, minutes))
        if path == "/api/gesture":
            zone = payload.get("zone")
            if self.companion.hardware.port:
                return self.json_response(409, {"error": "当前启用了实体传感器"})
            if zone not in ("left", "right", "both"):
                return self.json_response(400, {"error": "未知感应区"})
            accepted = self.companion.hardware.simulate(zone)
            return self.json_response(200, {"accepted": accepted, "state": self.companion.state()})
        text = payload.get("text")
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 240 or "\x00" in text:
            return self.json_response(400, {"error": "说点什么吧，最多 240 字。"})
        result = self.companion.chat(text.strip())
        if result is None:
            return self.json_response(409, {"error": "小黑还在想上一句话。"})
        return self.json_response(200, result)


class Server(ThreadingHTTPServer):
    def __init__(self, port, companion):
        self.companion = companion
        super().__init__(("127.0.0.1", port), partial(Handler, companion=companion, port=port))

    def service_actions(self):
        self.companion.tick()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8780")))
    parser.add_argument("--serial", default=os.getenv("GUANGZAI_SERIAL", ""),
                        help="e.g. COM3 or /dev/serial/by-id/…; omit for simulator")
    parser.add_argument("--data-dir", type=Path,
                        default=Path(os.getenv("GUANGZAI_DATA_DIR", ROOT / "data")),
                        help="directory for the local SQLite store")
    parser.add_argument("--open", action="store_true", help="open the display in the default browser")
    parser.add_argument("--demo", action="store_true", help="use faster idle motion and conversation timing")
    parser.add_argument("--deepseek", action="store_true", help="use DeepSeek Flash for free chat")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    provider = None
    if args.deepseek:
        key = os.getenv("DEEPSEEK_API_KEY", "").strip()
        if not key and sys.stdin.isatty():
            key = getpass("DeepSeek API key (hidden): ").strip()
        if not key:
            parser.error("--deepseek needs DEEPSEEK_API_KEY or a key entered at the local prompt")
        provider = ChatProvider(url="https://api.deepseek.com/chat/completions", model="deepseek-flash", key=key)
    bridge = HardwareBridge(args.serial)
    companion = Companion(args.data_dir / "guangzai.sqlite3", bridge, provider, demo=args.demo)
    with Server(args.port, companion) as server:
        bridge.start()
        print(f"Guangzai  http://127.0.0.1:{args.port}/", flush=True)
        print(f"Input: {args.serial or 'simulator'} | Chat: {'configured AI' if companion.provider.enabled else 'local'}",
              flush=True)
        if args.open:
            webbrowser.open(f"http://127.0.0.1:{args.port}/")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            bridge.close()


if __name__ == "__main__":
    main()
