"""Check the delivered Nginx snippets using local synthetic HTTP upstreams.

Usage: python scripts/test_single_entry_nginx.py [--nginx /path/to/nginx]
Requires nginx on PATH; never connects to the deployment or uses real patient data.
"""
from __future__ import annotations

import argparse
import http.client
import json
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


ROOT = Path(__file__).resolve().parents[1]
HIDDEN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class Echo(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        self.respond()

    def do_POST(self):
        self.respond()

    def respond(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        result = json.dumps({
            "path": self.path, "body": body.decode(),
            "host": self.headers.get("Host"),
            "forwarded_host": self.headers.get("X-Forwarded-Host"),
            "proto": self.headers.get("X-Forwarded-Proto"),
            "cookie": self.headers.get("Cookie"),
            "authorization": self.headers.get("Authorization"),
            "entry": self.headers.get("X-FM-Entry"),
            "bff": self.headers.get("X-FM-Session-BFF"),
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Set-Cookie", "fm_session=synthetic; HttpOnly; Path=/")
        self.send_header("Content-Length", str(len(result)))
        self.end_headers()
        self.wfile.write(result)


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def probe(port, path, method="GET", body=None, headers=None, source=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5, source_address=source)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nginx", default=shutil.which("nginx"))
    args = parser.parse_args()
    if not args.nginx:
        parser.error("nginx is required on PATH or via --nginx")
    nginx = str(Path(args.nginx).resolve())
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Echo)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    public_port, admin_port = free_port(), free_port()
    while admin_port == public_port:
        admin_port = free_port()
    process = None
    try:
        with tempfile.TemporaryDirectory(prefix="fm-nginx-check-") as directory:
            prefix = Path(directory)
            (prefix / "logs").mkdir()
            (prefix / "temp").mkdir()
            (prefix / "html").mkdir()
            (prefix / "html/index.html").write_text("VUE_TEST_SHELL", encoding="utf-8")
            snippet_path = prefix / "fm-next-proxy.conf"
            snippet_path.write_text(
                (ROOT / "deploy/nginx/fm-next-proxy.conf.example").read_text(encoding="utf-8")
                .replace("172.16.0.140:3100", f"127.0.0.1:{upstream.server_port}"),
                encoding="utf-8",
            )
            locations = (ROOT / "deploy/nginx/joolun-single-entry.locations.conf.example").read_text(encoding="utf-8")
            private = (ROOT / "deploy/nginx/joolun-private-admin.conf.example").read_text(encoding="utf-8")
            # Replace the include filename, not arbitrary response paths.
            locations = locations.replace("/etc/nginx/snippets/fm-next-proxy.conf;", f'"{snippet_path.as_posix()}";')
            private = private.replace("/etc/nginx/snippets/fm-next-proxy.conf;", f'"{snippet_path.as_posix()}";')
            config = f"""
worker_processes 1;
error_log logs/error.log;
pid logs/nginx.pid;
events {{ worker_connections 64; }}
http {{
    access_log off;
    server {{
        listen 127.0.0.1:{public_port};
        server_name localhost;
        root "{(prefix / 'html').as_posix()}";
        {locations}
        location /prod-api/ {{ proxy_pass http://127.0.0.1:{upstream.server_port}/; }}
        location ~* \\.(js|css)$ {{ return 418; }}
        location / {{ try_files $uri $uri/ /index.html; }}
    }}
    server {{
        listen 127.0.0.1:{admin_port};
        server_name localhost;
        {private}
    }}
}}
"""
            (prefix / "nginx.conf").write_text(config, encoding="utf-8")
            command = [nginx, "-p", prefix.as_posix() + "/", "-c", "nginx.conf"]
            checked = subprocess.run(command + ["-t"], cwd=directory, capture_output=True, text=True, creationflags=HIDDEN)
            if checked.returncode:
                error_log = prefix / "logs/error.log"
                raise RuntimeError(checked.stdout + checked.stderr + (error_log.read_text(errors="replace") if error_log.exists() else "Nginx configuration check failed without a log"))
            process = subprocess.Popen(command + ["-g", "daemon off;"], cwd=directory, creationflags=HIDDEN)
            try:
                for _ in range(50):
                    try:
                        if probe(public_port, "/")[0] == 200:
                            break
                    except OSError:
                        pass
                    time.sleep(0.1)
                else:
                    raise AssertionError("Nginx did not become ready")

                assert probe(public_port, "/doctor/workstation")[2] == b"VUE_TEST_SHELL"
                paths = [
                    "/integration/embed", "/integration/embed/start?ticket=synthetic",
                    "/integration/embed/cases/case_synthetic", "/_next/static/chunks/app.js",
                    "/_next/static/css/app.css", "/api/internal/health", "/integration/embed/session",
                    "/api/v2", "/api/v2/cases/case_synthetic?offset=0",
                    "/api/v2/drafts/synthetic/report.pdf",

                ]
                for path in paths:
                    status, response_headers, body = probe(public_port, path)
                    assert status == 200, (path, status)
                    assert json.loads(body)["path"] == path
                    assert response_headers["Cache-Control"] == "no-store"
                    assert "HttpOnly" in response_headers["Set-Cookie"]

                for blocked in ['/api/internal', '/api/internal/cases', '/api/internal/auth/login', '/api/internal/api/v2/integrations/joolun/embed-sessions', '/api/v1', '/api/v1/auth/token']:
                    assert probe(public_port, blocked, headers={'X-FM-Entry':'admin'})[0] == 404
                assert probe(public_port, '/api/internal/health', 'POST', '')[0] == 403
                forged = json.loads(probe(public_port, '/api/v2/cases', headers={'X-FM-Entry':'admin', 'X-FM-Session-BFF':'1'})[2])
                assert forged['entry'] == 'public' and forged['bff'] is None
                internal = json.loads(probe(admin_port, '/products')[2])
                assert internal['entry'] == 'admin'
                body = '{"synthetic":true}'
                status, _, result = probe(public_port, "/api/v2/cases?test=1", "POST", body, {
                    "Host": "paracelsus.huapont.com.cn:8600",
                    "Cookie": "fm_session=synthetic",
                    "Authorization": "Bearer synthetic",
                    "Content-Type": "application/json",
                })
                result = json.loads(result)
                assert status == 200 and result["body"] == body
                assert result["host"] == result["forwarded_host"] == "paracelsus.huapont.com.cn:8600"
                assert result["proto"] == "http"  # Local fixture; HTTPS is supplied by production TLS.
                assert result["cookie"] == "fm_session=synthetic"
                assert result["authorization"] == "Bearer synthetic"
                assert json.loads(probe(public_port, "/prod-api/admin/api/doctor/home")[2])["path"] == "/admin/api/doctor/home"
                assert json.loads(probe(admin_port, "/products")[2])["path"] == "/products"
                assert probe(admin_port, "/products", source=("127.0.0.2", 0))[0] == 403
                print(f"PASS: nginx -t; {len(paths)} AI routes; Vue fallback; Java prefix; headers/body; private admin allow/deny")
            finally:
                subprocess.run(command + ["-s", "quit"], check=False, creationflags=HIDDEN, capture_output=True)
                process.wait(timeout=10)
                process = None
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
        upstream.shutdown()
        upstream.server_close()


if __name__ == "__main__":
    main()
