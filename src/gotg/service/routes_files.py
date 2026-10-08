"""`/games` and `/files`: streaming a member file or a hand-placed key.

These are the routes that move gigabytes, and they are their own file because
the byte-range arithmetic had been buried inside the same method that sends
the headers: `_stream_fd` decided a window, refused a stream slot and wrote
seven headers in fifty lines. `range_window` is now the whole range decision
as a pure function (a 416, an ignored header, a 206 and a stale If-Range are
each one assertion), and `open_contained` -- the check that a served file is
really under its root -- sits beside the only two routes that use it.
"""

from __future__ import annotations

import os
import re
import stat
import sys
import urllib.parse
from pathlib import Path

# A single byte-range request: bytes=N- or bytes=N-M. Multi-range answers 200
# with the whole file rather than a multipart body nothing here needs. The
# digit bound matters: int() on thousands of digits raises, and an absurd
# range should fall through to a plain 200, not a traceback.
RANGE_RE = re.compile(r"^bytes=([0-9]{1,18})-([0-9]{0,18})$")

# What may be served from the files directory: keys and firmware names.
# No leading dot by construction (the first class excludes it), no slash by
# split, and the directory a person curates is the real allowlist.
FILES_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def open_contained(path: Path, root: Path) -> int | None:
    """An fd for a regular file provably under root, or None.

    The check that counts is on what was actually opened: O_NOFOLLOW refuses a
    symlink as the final component, and the /proc re-check catches a
    retargeted directory on the way there — a path checked and then opened is
    two syscalls with a race between them.
    """
    try:
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except OSError:
        return None
    real = Path(os.path.realpath(f"/proc/self/fd/{fd}"))
    if not stat.S_ISREG(os.fstat(fd).st_mode) or not real.is_relative_to(Path(os.path.realpath(root))):
        os.close(fd)
        return None
    return fd


def range_window(range_header: str, if_range: str, etag: str | None, size: int) -> tuple[int, int, int]:
    """`(start, end, status)` for a file of `size` bytes: 200 for the whole of
    it, 206 for the window asked for, 416 when the window starts past the end.

    `end` is inclusive. A Range that does not parse, a validator that is not
    the file's current ETag and an inverted range are all the same answer, the
    whole file: an invalid Range is ignored, not refused (RFC 9110 §14.1.1).
    """
    start, end, status = 0, size - 1, 200
    wanted = RANGE_RE.match(range_header)
    # If-Range with a stale validator means the bytes changed since
    # the client's partial: send the whole file rather than splice.
    if wanted and (not if_range or (etag is not None and if_range == etag)):
        start = int(wanted.group(1))
        if wanted.group(2):
            end = min(int(wanted.group(2)), size - 1)
        if start >= size:
            return start, end, 416
        if start > end:
            return 0, size - 1, 200
        status = 206
    return start, end, status


class FileRoutes:
    def _games(self, rest: str) -> None:
        """`GET /games/<platform>/<id>/<name>` streams one member file.

        Everything about the response is resumable: Accept-Ranges, a single
        byte range honored with 206, the sha256 as an ETag so If-Range makes a
        resume against changed bytes restart cleanly instead of splicing two
        files together.
        """
        if self.catalog is None:
            self._problem(503, "this service holds no catalog")
            return
        if self.command not in ("GET", "HEAD"):
            self._problem(405, f"{self.command} is not something the library answers")
            return

        parts = self._split(rest)
        if parts is None:
            return
        segments = [urllib.parse.unquote(s) for s in parts.path.strip("/").split("/") if s]
        if len(segments) < 3:
            self._problem(404, "a file lives at /games/<platform>/<id>/<name>")
            return
        # A member name may nest — a WiiU dump is fetched as its tree.
        platform, game_id = segments[0], segments[1]
        name = "/".join(segments[2:])

        found = self.catalog.open_member(platform, game_id, name)
        if found is None:
            self._problem(404, f"no such file: {platform}/{game_id}/{name}")
            return
        meta, fd = found
        if fd is None:
            # The row exists and the bytes do not: the index is stale, and
            # that is server news, not client news.
            print(f"catalog names a missing file: {platform}/{game_id}/{name}", file=sys.stderr)
            self._problem(404, f"no such file: {platform}/{game_id}/{name}")
            return
        try:
            self._stream_fd(fd, name, meta.get("sha256"))
        finally:
            os.close(fd)

    def _files(self, rest: str) -> None:
        """`GET /files/<platform>/<name>` — keys and firmware, hand-placed.

        The curated directory is the allowlist; this only insists the name is
        shaped like a file someone would place there, and that what opens is a
        regular file inside it. 404 for everything absent, so the client's
        missing-keys path stays a warning.
        """
        if self.files_dir is None:
            self._problem(503, "this service holds no files directory")
            return
        if self.command not in ("GET", "HEAD"):
            self._problem(405, f"{self.command} is not something the files answer")
            return

        parts = self._split(rest)
        if parts is None:
            return
        segments = [urllib.parse.unquote(s) for s in parts.path.strip("/").split("/") if s]
        if len(segments) != 2:
            self._problem(404, "a file lives at /files/<platform>/<name>")
            return
        platform, name = segments
        if not re.match(r"^[a-z0-9][a-z0-9_-]{0,15}$", platform) or not FILES_NAME_RE.match(name):
            self._problem(404, f"no such file: {platform}/{name}")
            return

        fd = open_contained(self.files_dir / platform / name, self.files_dir)
        if fd is None:
            self._problem(404, f"no such file: {platform}/{name}")
            return
        try:
            self._stream_fd(fd, name, None)
        finally:
            os.close(fd)

    def _stream_fd(self, fd: int, name: str, sha256: str | None) -> None:
        size = os.fstat(fd).st_size
        etag = f'"{sha256}"' if sha256 else None
        start, end, status = range_window(self.headers.get("Range", ""), self.headers.get("If-Range", ""), etag, size)
        if status == 416:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        streaming = self.command == "GET"
        # The cap is what keeps the saves and artwork halves answering while
        # multi-gigabyte pulls are in flight — a thread per TCP connection has
        # no other limit.
        if streaming and not self.streams.acquire(blocking=False):
            self.send_response(503)
            self.send_header("Retry-After", "5")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        try:
            count = end - start + 1
            self._send_file_headers(status, name, etag, start, end, size)
            if streaming:
                self._send_range(fd, start, count)
        finally:
            if streaming:
                self.streams.release()

    def _send_file_headers(self, status: int, name: str, etag: str | None, start: int, end: int, size: int) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        if etag:
            self.send_header("ETag", etag)
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header(
            "Content-Disposition",
            f"attachment; filename*=UTF-8''{urllib.parse.quote(name.rsplit('/', 1)[-1])}",
        )
        self.end_headers()

    def _send_range(self, fd: int, offset: int, remaining: int) -> None:
        # socket.sendfile rather than a hand-rolled os.sendfile loop: it
        # handles partial sends, EAGAIN under the socket timeout, and falls
        # back to plain send() where sendfile is unsupported — and the
        # timeout is what stops a client that quit reading from holding a
        # stream slot forever.
        self.wfile.flush()
        if remaining <= 0:
            # socket.sendfile treats a falsy count as "the whole file".
            return
        sent = 0
        try:
            with open(fd, "rb", buffering=0, closefd=False) as src:
                sent = self.connection.sendfile(src, offset, remaining)
        except OSError:
            pass
        if sent < remaining:
            # A short body desynchronizes a kept-alive connection.
            self.close_connection = True
