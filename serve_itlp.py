#!/usr/bin/env python3
"""
Local HTTP Server with Byte-Range Support (HTTP 206 Partial Content)
====================================================================

Enables smooth seeking/scrubbing in HTML5 `<video>` elements (.m4v / .mp4)
when serving patched iTunes LP packages locally.
"""

import ctypes
import http.server
import locale
import os
import re
import socketserver
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PORT = 8000


def is_german_system() -> bool:
    """Returns True if the OS locale is German."""
    for env_var in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        val = os.environ.get(env_var, "").strip().lower()
        if val.startswith("de"):
            return True
        if val and val not in ("c", "posix"):
            return False

    if sys.platform == "win32":
        try:
            lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            if (lang_id & 0xFF) == 0x07:
                return True
        except Exception:
            pass

    try:
        loc = locale.getlocale()[0] or ""
        if loc.lower().startswith("de"):
            return True
    except Exception:
        pass

    return False


class RangeHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    """HTTP request handler supporting `Range: bytes=start-end` requests."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def send_head(self):
        path = self.translate_path(self.path)
        if os.path.isdir(path):
            return super().send_head()

        ctype = self.guess_type(path)
        try:
            f = open(path, "rb")
        except OSError:
            self.send_error(404, "File not found")
            return None

        fs = os.fstat(f.fileno())
        file_len = fs.st_size
        range_header = self.headers.get("Range")

        if range_header:
            m = re.match(r"bytes=(\d+)-(\d*)", range_header)
            if m:
                start = int(m.group(1))
                end = int(m.group(2)) if m.group(2) else file_len - 1
                end = min(end, file_len - 1)
                if start >= file_len or start > end:
                    f.close()
                    self.send_error(416, "Requested Range Not Satisfiable")
                    return None

                length = end - start + 1
                self.send_response(206)
                self.send_header("Content-type", ctype)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Range", f"bytes {start}-{end}/{file_len}")
                self.send_header("Content-Length", str(length))
                self.send_header("Last-Modified", self.date_time_string(fs.st_mtime))
                self.end_headers()
                f.seek(start)
                return _LimitedFile(f, length)

        self.send_response(200)
        self.send_header("Content-type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(file_len))
        self.send_header("Last-Modified", self.date_time_string(fs.st_mtime))
        self.end_headers()
        return f


class _LimitedFile:
    """File-like wrapper that reads at most `remaining` bytes."""

    def __init__(self, f, remaining: int):
        self.f = f
        self.remaining = remaining

    def read(self, size: int = -1) -> bytes:
        if self.remaining <= 0:
            return b""
        if size < 0 or size > self.remaining:
            size = self.remaining
        data = self.f.read(size)
        self.remaining -= len(data)
        return data

    def close(self) -> None:
        self.f.close()


class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


if __name__ == "__main__":
    with ThreadingHTTPServer(("127.0.0.1", PORT), RangeHTTPRequestHandler) as httpd:
        if is_german_system():
            print(f"iTunes LP Server mit Video-Seeking aktiv unter http://127.0.0.1:{PORT}")
        else:
            print(f"iTunes LP server with video seeking running at http://127.0.0.1:{PORT}")
        httpd.serve_forever()