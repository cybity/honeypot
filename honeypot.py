import os
import socket
import sys
import threading
import time

from config import (
    DATA_DIR,
    DB_PATH,
    DASHBOARD_HOST,
    DASHBOARD_PORT,
    HTTP_PORT,
    SSH_PORT,
)
from storage import Database
import http_pot
import ssh_pot
from dashboard import create_app


def preflight(ports):
    for port in ports:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            probe.bind(("0.0.0.0", port))
        except OSError:
            print(f"[!] Port {port} already in use - free it or change config.py")
            sys.exit(1)
        finally:
            probe.close()


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    preflight([SSH_PORT, HTTP_PORT, DASHBOARD_PORT])
    db = Database(DB_PATH)

    stop_event = threading.Event()

    ssh_thread = threading.Thread(target=ssh_pot.serve, args=(db, stop_event),
                                  daemon=True, name="ssh-pot")
    ssh_thread.start()

    http_server = http_pot.make_server(db)
    http_thread = threading.Thread(target=http_server.serve_forever,
                                   kwargs={"poll_interval": 0.5},
                                   daemon=True, name="http-pot")
    http_thread.start()

    app = create_app(db)
    dash_thread = threading.Thread(
        target=lambda: app.run(host=DASHBOARD_HOST, port=DASHBOARD_PORT,
                               use_reloader=False, threaded=True),
        daemon=True, name="dashboard")
    dash_thread.start()

    print("=" * 58)
    print(" honeypot is live - all activity is fake and being logged")
    print(f"   SSH pot      : ssh -p {SSH_PORT} admin@<this-host>")
    print(f"   HTTP pot     : http://<this-host>:{HTTP_PORT}/")
    print(f"   Dashboard    : http://{DASHBOARD_HOST}:{DASHBOARD_PORT}/")
    print(f"   Database     : {DB_PATH}")
    print(" press Ctrl+C to stop")
    print("=" * 58)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[*] shutting down...")
        stop_event.set()
        http_server.shutdown()
        db.close()


if __name__ == "__main__":
    main()
