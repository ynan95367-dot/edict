import socket
import threading
import time

import pytest


@pytest.fixture
def sse_server(tmp_path, monkeypatch):
    import server as srv
    monkeypatch.setattr(srv, "DATA", tmp_path, raising=False)
    monkeypatch.setattr(srv, "_ACTIVE_TASK_DATA_DIR", tmp_path, raising=False)
    (tmp_path / "live_status.json").write_text("{}", encoding="utf-8")
    httpd = srv.ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        yield srv, tmp_path, port
    finally:
        httpd.shutdown()
        httpd.server_close()


def _raw_sse_connect(port, timeout=5):
    """Open a raw TCP connection to /api/stream.

    Returns (sock, status_line_str, headers_dict, raw_fp).
    raw_fp is a socket makefile("rb") for line-level reads.
    The socket timeout is set to `timeout`.
    """
    sock = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    sock.settimeout(timeout)
    sock.sendall(b"GET /api/stream HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: keep-alive\r\n\r\n")
    # We need a file-like for readline, but must adjust timeout per-call.
    # Keep the socket object so we can call settimeout() before each read.
    return sock


def _recv_lines_until(sock, needle: bytes, deadline: float) -> bytes:
    """Read from sock character-by-character until needle found or deadline."""
    accumulated = b""
    buf = b""
    while time.time() < deadline:
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        sock.settimeout(min(remaining, 1.0))
        try:
            chunk = sock.recv(256)
        except socket.timeout:
            continue
        except OSError:
            break
        if not chunk:
            break
        buf += chunk
        accumulated += chunk
        if needle in accumulated:
            break
    return accumulated


def _read_http_response(sock, deadline: float):
    """Read HTTP response headers from sock; return (status_line, headers_dict)."""
    raw = b""
    while b"\r\n\r\n" not in raw and time.time() < deadline:
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        sock.settimeout(min(remaining, 2.0))
        try:
            chunk = sock.recv(512)
        except socket.timeout:
            continue
        if not chunk:
            break
        raw += chunk
    header_part, _, _ = raw.partition(b"\r\n\r\n")
    lines = header_part.split(b"\r\n")
    status_line = lines[0].decode(errors="replace") if lines else ""
    headers = {}
    for line in lines[1:]:
        if b":" in line:
            k, _, v = line.decode(errors="replace").partition(":")
            headers[k.strip().lower()] = v.strip()
    return status_line, headers


def test_stream_sends_event_stream_headers_and_initial_event(sse_server):
    _srv, _dir, port = sse_server
    sock = _raw_sse_connect(port, timeout=5)
    try:
        deadline = time.time() + 5
        status_line, headers = _read_http_response(sock, deadline)
        assert "200" in status_line
        assert "text/event-stream" in headers.get("content-type", "")
        # initial frame is written immediately on connect
        chunk = _recv_lines_until(sock, b"live-status", deadline)
        assert b"live-status" in chunk
    finally:
        sock.close()


def test_stream_emits_on_live_status_change(sse_server):
    _srv, data_dir, port = sse_server
    sock = _raw_sse_connect(port, timeout=8)
    try:
        # consume initial frame + headers
        init_deadline = time.time() + 5
        _read_http_response(sock, init_deadline)
        _recv_lines_until(sock, b"live-status", init_deadline)
        time.sleep(0.2)
        # mutate live_status.json -> bump mtime
        (data_dir / "live_status.json").write_text('{"tasks":{}}', encoding="utf-8")
        # next change frame should arrive within the ~1s watch interval
        deadline = time.time() + 6
        got = _recv_lines_until(sock, b"live-status", deadline)
        assert b"live-status" in got
    finally:
        sock.close()
