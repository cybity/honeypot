from config import BIND_HOST, HTTP_PORT
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

LOGIN_PAGE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>192.168.0.1 | Router Login</title>
<style>
body { font-family: Arial, sans-serif; background: #e8ecef; margin: 0; }
.header { background: #1a2b4a; color: #fff; padding: 18px 32px; font-size: 20px; }
.card { max-width: 380px; margin: 60px auto; background: #fff; border-radius: 6px;
        padding: 28px 32px; box-shadow: 0 2px 8px rgba(0,0,0,.15); }
.card h1 { font-size: 17px; color: #1a2b4a; margin-top: 0; }
label { display: block; margin: 14px 0 4px; font-size: 13px; color: #444; }
input[type=text], input[type=password] { width: 100%; padding: 9px; border: 1px solid #bbb;
        border-radius: 4px; box-sizing: border-box; }
button { margin-top: 20px; width: 100%; padding: 10px; background: #1a73c7; color: #fff;
        border: 0; border-radius: 4px; font-size: 15px; cursor: pointer; }
.foot { text-align: center; color: #888; font-size: 12px; padding: 24px; }
</style>
</head>
<body>
<div class="header">NetGear ProSafe &mdash; Web Management Console</div>
<div class="card">
<h1>Sign in to your router</h1>
<form method="POST" action="/login">
<label>Username</label><input type="text" name="username" autofocus>
<label>Password</label><input type="password" name="password">
<button type="submit">Login</button>
</form>
</div>
<div class="foot">Firmware V1.2.0.44 &middot; 192.168.0.1</div>
</body>
</html>
"""

ROBOTS = "User-agent: *\nDisallow: /admin\nDisallow: /backup\n"

NOT_FOUND = (
    "<html>\r\n<head><title>404 Not Found</title></head>\r\n"
    "<body>\r\n<center><h1>404 Not Found</h1></center>\r\n"
    "<hr><center>nginx/1.18.0 (Ubuntu)</center>\r\n</body>\r\n</html>\r\n"
)


def make_handler(db):
    class PotRequestHandler(BaseHTTPRequestHandler):
        server_version = "nginx/1.18.0 (Ubuntu)"
        sys_version = ""
        protocol_version = "HTTP/1.1"

        def version_string(self):
            return self.server_version

        def _client(self):
            return self.client_address[0], self.client_address[1]

        def _read_body(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                return ""
            return self.rfile.read(min(length, 65536)).decode("utf-8", errors="replace")

        def _record(self, code, body=None):
            ip, port = self._client()
            headers = {k.lower(): v for k, v in self.headers.items()}
            db.log(
                "http_request", service="http", src_ip=ip, src_port=port,
                detail=f"{self.command} {self.path} -> {code}",
                extra={"headers": headers, "body": body},
            )

        def _respond(self, code, payload="", content_type="text/html"):
            data = payload.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            self._record(code)

        def _redirect(self, location):
            self.send_response(302)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()
            self._record(302)

        def do_GET(self):
            path = self.path.split("?")[0]
            if path in ("/", "/index.html", "/login"):
                self._respond(200, LOGIN_PAGE)
            elif path == "/robots.txt":
                self._respond(200, ROBOTS, content_type="text/plain")
            else:
                self._respond(404, NOT_FOUND)

        def do_HEAD(self):
            self.do_GET()

        def do_POST(self):
            path = self.path.split("?")[0]
            body = self._read_body()
            fields = parse_qs(body or "")
            username = (fields.get("username") or fields.get("user") or
                        fields.get("login") or [""])[0]
            password = (fields.get("password") or fields.get("pass") or
                        fields.get("passwd") or [""])[0]
            if path in ("/login", "/admin/login", "/api/login"):
                ip, _ = self._client()
                db.log("auth", service="http", src_ip=ip,
                       detail="web login form",
                       extra={"form_path": path})
                db.log("login", service="http", src_ip=ip,
                       username=username, password=password, detail="accepted")
                self._redirect("/")
            else:
                self._respond(404, NOT_FOUND)

        def do_PUT(self):
            self._read_body()
            self._respond(404, NOT_FOUND)

        def do_DELETE(self):
            self._respond(404, NOT_FOUND)

        def do_OPTIONS(self):
            self.send_response(200)
            self.send_header("Allow", "GET, HEAD, POST, OPTIONS")
            self.send_header("Content-Length", "0")
            self.end_headers()
            self._record(200)

        def log_message(self, format, *args):
            pass

    return PotRequestHandler


def make_server(db):
    server = ThreadingHTTPServer((BIND_HOST, HTTP_PORT), make_handler(db))
    server.daemon_threads = True
    return server
