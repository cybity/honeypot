import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "honeypot.db")
HOST_KEY_PATH = os.path.join(DATA_DIR, "ssh_host_rsa_key")

BIND_HOST = "0.0.0.0"
SSH_PORT = 2222
HTTP_PORT = 8080
DASHBOARD_HOST = "127.0.0.1"
DASHBOARD_PORT = 5000

FAKE_HOSTNAME = "web01"
FAKE_USER = "admin"
FAKE_KERNEL = "5.15.0-91-generic"
FAKE_OS = "Ubuntu 22.04.3 LTS"
FAKE_IP = "203.0.113.10"
FAKE_INTERNAL_IP = "10.0.2.15"
FAKE_MAC = "de:ad:be:ef:00:01"

BLACKLIST_PASSWORDS = {"honeypot", "honeyp", "pott"}
