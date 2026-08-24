import posixpath
import re
import socket
import threading
import time
from datetime import datetime, timezone

import paramiko

from config import (
    BIND_HOST,
    BLACKLIST_PASSWORDS,
    FAKE_HOSTNAME,
    FAKE_INTERNAL_IP,
    FAKE_IP,
    FAKE_KERNEL,
    FAKE_OS,
    FAKE_MAC,
    HOST_KEY_PATH,
    SSH_PORT,
)

SERVER_VERSION = "SSH-2.0-OpenSSH_8.9p1 Ubuntu-3ubuntu0.4"


def load_or_create_host_key(path):
    try:
        return paramiko.RSAKey.from_private_key_file(path)
    except FileNotFoundError:
        key = paramiko.RSAKey.generate(2048)
        key.write_private_key_file(path)
        return key


class FakeFilesystem:
    def __init__(self, user):
        self.user = user
        self.home = f"/home/{user}"
        self.cwd = self.home
        self.entries = {}
        dirs = [
            "/", "/bin", "/sbin", "/etc", "/etc/ssh", "/usr", "/usr/bin",
            "/var", "/var/log", "/var/www", "/tmp", "/opt", "/proc", "/dev",
            "/run", "/boot", "/srv", "/mnt", "/lib", self.home,
        ]
        for d in dirs:
            self.entries[d] = None
        self.entries["/etc/passwd"] = (
            "root:x:0:0:root:/root:/bin/bash\n"
            "daemon:x:1:1:daemon:/usr/sbin:/usr/sbin/nologin\n"
            "www-data:x:33:33:www-data:/var/www:/usr/sbin/nologin\n"
            "sshd:x:105:65534::/run/sshd:/usr/sbin/nologin\n"
            f"{user}:x:1000:1000::/{self.home.lstrip('/')}:/bin/bash\n"
        )
        self.entries["/etc/shadow"] = (
            "root:*:19100:0:99999:7:::\n"
            f"{user}:$6$rounds=4096$saltdeadbeef$:19100:0:99999:7:::\n"
        )
        self.entries["/etc/os-release"] = (
            'PRETTY_NAME="Ubuntu 22.04.3 LTS"\n'
            'NAME="Ubuntu"\nVERSION_ID="22.04"\n'
            'ID=ubuntu\nHOME_URL="https://www.ubuntu.com/"\n'
        )
        self.entries["/etc/hostname"] = f"{FAKE_HOSTNAME}\n"
        self.entries["/etc/hosts"] = (
            "127.0.0.1 localhost\n"
            f"{FAKE_IP} {FAKE_HOSTNAME}\n"
        )
        self.entries["/etc/group"] = (
            "root:x:0:\nsudo:x:27:user\nsshd:x:105:\n"
            f"{user}:x:1000:\n"
        )
        self.entries["/etc/ssh/sshd_config"] = (
            "Port 22\nPermitRootLogin yes\nPasswordAuthentication yes\n"
        )
        self.entries[f"{self.home}/.bashrc"] = (
            "case $- in *i*) ;; *) return;; esac\nHISTCONTROL=ignoreboth\n"
            "alias ll='ls -alF'\nexport PS1='\\u@\\h:\\w\\$ '\n"
        )
        self.entries[f"{self.home}/.profile"] = "if [ -f ~/.bashrc ]; then . ~/.bashrc; fi\n"
        self.entries[f"{self.home}/notes.txt"] = (
            "TODO: rotate api keys before going live\n"
            "db backup password is in keepass on the office pc\n"
        )
        self.entries["/var/log/auth.log"] = (
            "Aug 20 09:14:01 web01 sshd[821]: Accepted password for admin from 10.0.2.2 port 55412 ssh2\n"
            "Aug 21 03:02:11 web01 CRON[1143]: pam_unix(cron:session): session opened for user root\n"
        )
        self.entries["/var/log/syslog"] = (
            "Aug 23 07:40:01 web01 systemd[1]: logrotate.service: Succeeded.\n"
            "Aug 23 08:17:44 web01 kernel: [ 8812.331] eth0: link up, 100Mbps\n"
        )
        self.entries["/var/www/index.html"] = "<html><body><h1>It works!</h1></body></html>\n"

    def normalize(self, path):
        if not path:
            path = "."
        if not path.startswith("/"):
            path = posixpath.join(self.cwd, path)
        return posixpath.normpath(path)

    def exists(self, path):
        return self.normalize(path) in self.entries

    def is_dir(self, path):
        p = self.normalize(path)
        return p in self.entries and self.entries[p] is None

    def read(self, path):
        return self.entries.get(self.normalize(path))

    def write(self, path, content=""):
        self.entries[self.normalize(path)] = content

    def mkdir(self, path):
        self.entries[self.normalize(path)] = None

    def remove(self, path):
        target = self.normalize(path)
        if target in ("/", self.home):
            return False
        removed_any = target in self.entries
        for key in [k for k in self.entries if k == target or k.startswith(target + "/")]:
            del self.entries[key]
        return removed_any

    def list_dir(self, path):
        base = self.normalize(path)
        names = []
        for key in self.entries:
            parent = posixpath.dirname(key)
            if parent == base and key != base:
                names.append(posixpath.basename(key))
        return sorted(names)

    def resolve_user_path(self, path):
        if not path:
            return self.cwd
        expanded = re.sub(r"^~(?=/|$)", self.home, path)
        return self.normalize(expanded)


class FakeShell:
    def __init__(self, db, username, src_ip):
        self.db = db
        self.username = username or "root"
        self.src_ip = src_ip
        self.fs = FakeFilesystem(self.username)
        self.history = []
        self.exit_requested = False
        self._boot_offset = 3600 * 19 + 1247

    @property
    def prompt(self):
        cwd_display = self.fs.cwd.replace(self.fs.home, "~", 1)
        return f"{self.username}@{FAKE_HOSTNAME}:{cwd_display}$ "

    def execute(self, line):
        line = line.strip()
        if not line:
            return ""
        self.db.log("command", service="ssh", src_ip=self.src_ip,
                    username=self.username, detail=line)
        try:
            tokens = tokenize(line)
        except ValueError:
            return ""
        return self._pipeline(tokens)

    def _pipeline(self, tokens):
        segments = []
        pipes = []
        current = []
        for token in tokens:
            if token == "|":
                pipes.append(current)
                current = []
            elif token in (";", "&&", "||"):
                pipes.append(current)
                segments.append(pipes[:])
                pipes = []
                current = []
            else:
                current.append(token)
        if current or pipes:
            pipes.append(current)
            segments.append(pipes)
        outputs = []
        for segment in segments:
            data = ""
            for argv in segment:
                if not argv:
                    return "bash: syntax error near unexpected token"
                data = self._run_stage(argv, data)
                if self.exit_requested:
                    break
            outputs.append(data)
        return "\n".join(output for output in outputs if output != "")

    def _run_stage(self, argv, stdin):
        cmd, args = argv[0], argv[1:]
        key = re.sub(r"[^a-z0-9_]", "", cmd.lower())
        if key in self.FILTERS:
            return getattr(self, f"_filter_{key}")(args, stdin)
        handler = getattr(self, f"cmd_{key}", None)
        if handler is None:
            return f"bash: {cmd}: command not found"
        return handler(args) or ""

    def sudo(self, args):
        if not args:
            return "usage: sudo command"
        if args[0] in ("-i", "-s"):
            return ""
        return self._pipeline(tokenize(" ".join(args)))

    def cmd_sudo(self, args):
        return self.sudo(args)

    FILTERS = {"grep", "head", "tail", "wc", "sort", "uniq", "cut", "awk"}

    def _with_files(self, args, stdin):
        parts = [stdin] if stdin else []
        for a in args:
            content = self.fs.read(a)
            if content and not self.fs.is_dir(a):
                parts.append(content.rstrip("\n"))
        return "\n".join(parts)

    def _filter_grep(self, args, stdin):
        invert = False
        ignore_case = False
        pattern = None
        rest = []
        for a in args:
            if a.startswith("-"):
                invert |= "v" in a
                ignore_case |= "i" in a
            elif pattern is None:
                pattern = a
            else:
                rest.append(a)
        if pattern is None:
            return "usage: grep [-iv] PATTERN [FILE]"
        text = self._with_files(rest, stdin)
        if ignore_case:
            pattern = pattern.lower()
        out = []
        for line in text.split("\n"):
            hay = line.lower() if ignore_case else line
            if (pattern in hay) != invert:
                out.append(line)
        return "\n".join(out)

    def _head_tail(self, args, stdin, from_top):
        count = 10
        rest = []
        i = 0
        while i < len(args):
            a = args[i]
            if a == "-n" and i + 1 < len(args):
                count = int(args[i + 1])
                i += 2
            elif a.startswith("-") and a[1:].isdigit():
                count = int(a[1:])
                i += 1
            elif a.startswith("-"):
                i += 1
            else:
                rest.append(a)
                i += 1
        lines = self._with_files(rest, stdin).split("\n")
        selected = lines[:count] if from_top else lines[-count:]
        return "\n".join(selected)

    def _filter_head(self, args, stdin):
        return self._head_tail(args, stdin, True)

    def _filter_tail(self, args, stdin):
        return self._head_tail(args, stdin, False)

    def _filter_wc(self, args, stdin):
        flag = next((a for a in args if a.startswith("-")), "")
        text = self._with_files([a for a in args if not a.startswith("-")], stdin)
        lines = len([l for l in text.split("\n") if l != ""]) if text else 0
        words = len(text.split())
        chars = len(text) + (1 if text and not text.endswith("\n") else 0)
        if "-l" in flag:
            return str(lines)
        if "-w" in flag:
            return str(words)
        if "-c" in flag:
            return str(chars)
        return f"{lines:>7} {words:>7} {chars:>7}"

    def _filter_sort(self, args, stdin):
        text = self._with_files([a for a in args if not a.startswith("-")], stdin)
        lines = sorted(text.split("\n"))
        if any(a for a in args if a.startswith("-") and "r" in a):
            lines.reverse()
        return "\n".join(lines)

    def _filter_uniq(self, args, stdin):
        counted = any(a for a in args if a.startswith("-") and "c" in a)
        text = self._with_files([a for a in args if not a.startswith("-")], stdin)
        out = []
        previous = None
        n = 0
        for line in text.split("\n"):
            if line == previous:
                n += 1
            else:
                if previous is not None:
                    out.append(f"{n:>7} {previous}" if counted else previous)
                previous = line
                n = 1
        if previous is not None:
            out.append(f"{n:>7} {previous}" if counted else previous)
        return "\n".join(out)

    def _filter_cut(self, args, stdin):
        delim = "\t"
        fields = "1"
        files = []
        i = 0
        while i < len(args):
            a = args[i]
            if a == "-d" and i + 1 < len(args):
                delim = args[i + 1]
                i += 2
            elif a.startswith("-d") and len(a) > 2:
                delim = a[2]
                i += 1
            elif a == "-f" and i + 1 < len(args):
                fields = args[i + 1]
                i += 2
            elif a.startswith("-f") and len(a) > 2:
                fields = a[2:]
                i += 1
            elif a.startswith("-"):
                i += 1
            else:
                files.append(a)
                i += 1
        text = self._with_files(files, stdin)
        wanted = set()
        for part in fields.split(","):
            part = part.strip()
            if not part:
                continue
            wanted.add(int(part))
        out = []
        for line in text.split("\n"):
            pieces = line.split(delim)
            picked = [pieces[j - 1] for j in sorted(wanted) if j <= len(pieces)]
            out.append(delim.join(picked))
        return "\n".join(out)

    def _filter_awk(self, args, stdin):
        delim = None
        program = ""
        files = []
        i = 0
        while i < len(args):
            a = args[i]
            if a == "-F" and i + 1 < len(args):
                delim = args[i + 1]
                i += 2
            elif a.startswith("-F") and len(a) > 2:
                delim = a[2:]
                i += 1
            elif "{" in a:
                program = a
                i += 1
            elif a.startswith("-"):
                i += 1
            else:
                files.append(a)
                i += 1
        match = re.search(r"\{\s*print\s+(.*?)\s*\}", program)
        if not match:
            return ""
        text = self._with_files(files, stdin)
        parts = re.findall(r"\$([0-9]+)", match.group(1))
        multi = "," in match.group(1)
        out = []
        for line in text.split("\n"):
            cols = line.split() if delim is None else line.split(delim)
            if not parts:
                out.append(line)
                continue
            vals = []
            for p in parts:
                idx = int(p)
                vals.append(cols[idx - 1] if idx <= len(cols) else "")
            sep = " " if multi else ""
            out.append(sep.join(vals))
        return "\n".join(out)

    cmd_doas = cmd_sudo

    def cmd_su(self, args):
        self.db.log("su_attempt", service="ssh", src_ip=self.src_ip,
                    username=self.username, detail=" ".join(args))
        time.sleep(0.5)
        return "su: Authentication failure"

    def cmd_whoami(self, args):
        return self.username

    def cmd_hostname(self, args):
        return FAKE_HOSTNAME

    def cmd_id(self, args):
        return (f"uid=1000({self.username}) gid=1000({self.username}) "
                f"groups=1000({self.username}),27(sudo)")

    def cmd_groups(self, args):
        return f"{self.username} sudo"

    def cmd_uname(self, args):
        if "-a" in args or "-r" in args:
            return (f"Linux {FAKE_HOSTNAME} {FAKE_KERNEL} "
                    "#101-Ubuntu SMP Thu Nov 16 14:20:11 UTC 2023 x86_64 x86_64 x86_64 GNU/Linux")
        return "Linux"

    def cmd_pwd(self, args):
        return self.fs.cwd

    def cmd_cd(self, args):
        target = self.fs.resolve_user_path(args[0] if args else self.fs.home)
        if self.fs.is_dir(target):
            self.fs.cwd = target
            return ""
        return f"cd: {args[0]}: No such file or directory"

    def cmd_ls(self, args):
        flags = set(a for a in args if a.startswith("-"))
        paths = [a for a in args if not a.startswith("-")] or ["."]
        out = []
        for p in paths:
            resolved = self.fs.resolve_user_path(p)
            if not self.fs.exists(resolved):
                out.append(f"ls: cannot access '{p}': No such file or directory")
                continue
            if self.fs.is_dir(resolved):
                names = self.fs.list_dir(resolved)
                if "-a" in "".join(flags):
                    names = ["."] + [".."] + names
                if "-l" in "".join(flags):
                    for n in names:
                        full = posixpath.join(resolved, n)
                        isdir = self.fs.is_dir(full) if self.fs.exists(full) else True
                        size = 4096 if isdir else len(self.fs.read(full).encode())
                        stamp = datetime.now(timezone.utc).strftime("%b %d %H:%M")
                        perms = "drwxr-xr-x" if isdir else "-rw-r--r--"
                        out.append(f"{perms} 1 {self.username} {self.username} {size:>6} {stamp} {n}")
                else:
                    rendered = "  ".join(n + "/" if self.fs.is_dir(posixpath.join(resolved, n)) else n
                                         for n in names)
                    out.append(rendered)
            else:
                out.append(p)
        return "\n".join(out)

    def cmd_cat(self, args):
        if not args:
            return ""
        out = []
        for p in args:
            resolved = self.fs.resolve_user_path(p)
            if not self.fs.exists(resolved):
                out.append(f"cat: {p}: No such file or directory")
            elif self.fs.is_dir(resolved):
                out.append(f"cat: {p}: Is a directory")
            elif resolved == "/etc/shadow":
                out.append(f"cat: {p}: Permission denied")
            else:
                out.append(self.fs.read(resolved).rstrip("\n"))
        return "\n".join(out)

    def cmd_echo(self, args):
        text = " ".join(args)
        if text.startswith('"') and text.endswith('"'):
            text = text[1:-1]
        elif text.startswith("'") and text.endswith("'"):
            text = text[1:-1]
        return text

    def cmd_clear(self, args):
        return "\x1b[2J\x1b[H"

    def cmd_exit(self, args):
        self.exit_requested = True
        return ""

    cmd_logout = cmd_exit

    def cmd_history(self, args):
        return "\n".join(f"  {i + 1}  {c}" for i, c in enumerate(self.history))

    def cmd_ps(self, args):
        return (
            "USER         PID %CPU %MEM VSZ   RSS TTY   STAT START  TIME COMMAND\n"
            "root           1  0.0  0.4 167312 11648 ?     Ss   Aug20   0:04 /sbin/init splash\n"
            "root         812  0.0  0.5  15436  8512 ?       Ss   Aug20   0:00 sshd: /usr/sbin/sshd -D\n"
            "root         941  0.0  0.7 484312 28124 ?       Ssl  Aug20   0:01 nginx: master process\n"
            "www-data     942  0.0  0.3  48968 12108 ?       S    Aug20   0:00 nginx: worker process\n"
            f"{self.username}      1204  0.0  0.2   7368  4980 pts/0  Ss   09:12   0:00 -bash"
        )

    def cmd_df(self, args):
        return (
            "Filesystem     1K-blocks    Used Available Use% Mounted on\n"
            "/dev/vda1       41152812 9871232  29165432  26% /\n"
            "tmpfs             999320    1180    998140   1% /run/shm\n"
            "/dev/vdb1      103081248 2204168  95625192   3% /data"
        )

    def cmd_free(self, args):
        return (
            "               total        used        free      shared  buff/cache   available\n"
            "Mem:         1998636      742108      184332       21340     1072196     1097316\n"
            "Swap:        2097148           0     2097148"
        )

    def cmd_uptime(self, args):
        now = datetime.now().strftime("%H:%M:%S")
        mins = int(self._boot_offset // 60)
        return (f" {now} up {mins // 60}:{mins % 60:02d},  1 user,  "
                "load average: 0.04, 0.09, 0.12")

    def cmd_ifconfig(self, args):
        return (
            "eth0: flags=4163<UP,BROADCAST,RUNNING,MULTICAST>  mtu 1500\n"
            f"        inet {FAKE_INTERNAL_IP}  netmask 255.255.255.0  broadcast 10.0.2.255\n"
            f"        ether {FAKE_MAC}  txqueuelen 1000  (Ethernet)\n"
            "lo: flags=73<UP,LOOPBACK,RUNNING>  mtu 65536\n"
            "        inet 127.0.0.1  netmask 255.0.0.0"
        )

    def cmd_ip(self, args):
        return (
            "1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536 state UNKNOWN\n"
            "    inet 127.0.0.1/8 scope host lo\n"
            "2: eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 state UP\n"
            f"    inet {FAKE_INTERNAL_IP}/24 brd 10.0.2.255 scope global eth0"
        )

    def cmd_netstat(self, args):
        return (
            "Active Internet connections (only servers)\n"
            "Proto Recv-Q Send-Q Local Address           Foreign Address         State\n"
            "tcp        0      0 0.0.0.0:22              0.0.0.0:*               LISTEN\n"
            "tcp        0      0 0.0.0.0:80              0.0.0.0:*               LISTEN\n"
            "tcp        0      0 0.0.0.0:443             0.0.0.0:*               LISTEN"
        )

    cmd_ss = cmd_netstat

    def cmd_ping(self, args):
        targets = [a for a in args if not a.startswith("-")]
        if not targets:
            return "ping: usage error: Destination address required"
        target = targets[0]
        lines = [f"PING {target} ({target}) 56(84) bytes of data."]
        for i in range(1, 5):
            lines.append(
                f"64 bytes from {target}: icmp_seq={i} ttl=56 time={9 + i}.3 ms"
            )
        lines.append(f"--- {target} ping statistics ---")
        lines.append("4 packets transmitted, 4 received, 0% packet loss, time 3005ms")
        return "\n".join(lines)

    def _download(self, url):
        self.db.log("download", service="ssh", src_ip=self.src_ip,
                    username=self.username, detail=url)
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        return (
            f"--{stamp}--  {url}\n"
            f"Resolving {'host' if url.startswith('http') else 'host'}... "
            "failed: Temporary failure in name resolution."
        )

    def cmd_wget(self, args):
        urls = [a for a in args if a.startswith(("http://", "https://", "ftp://"))]
        if not urls:
            urls = [a for a in args if not a.startswith("-")]
        if not urls:
            return "wget: missing URL"
        return self._download(urls[0])

    def cmd_curl(self, args):
        urls = [a for a in args if a.startswith(("http://", "https://"))]
        if not urls:
            urls = [a for a in args if not a.startswith("-")]
        if not urls:
            return "curl: no URL specified"
        self.db.log("download", service="ssh", src_ip=self.src_ip,
                    username=self.username, detail=urls[0])
        return "curl: (6) Could not resolve host"

    def cmd_git(self, args):
        if args and args[0] == "clone" and len(args) > 1:
            return self._download(args[1])
        if args and args[0] == "status":
            return "fatal: not a git repository (or any of the parent directories): .git"
        return ""

    def cmd_crontab(self, args):
        if args and args[0] == "-l":
            return ("*/15 * * * * /usr/local/bin/healthcheck.sh\n"
                    "0 3 * * * /usr/bin/apt-get -y autoremove")
        return ""

    def cmd_env(self, args):
        return (f"SHELL=/bin/bash\nPWD={self.fs.cwd}\nHOME=/home/{self.username}\n"
                "LANG=en_US.UTF-8\nTERM=xterm-256color\n"
                "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")

    cmd_printenv = cmd_env

    def cmd_mkdir(self, args):
        targets = [a for a in args if not a.startswith("-")]
        for t in targets:
            if self.fs.exists(t):
                return f"mkdir: cannot create directory '{t}': File exists"
            self.fs.mkdir(t)
        return ""

    def cmd_touch(self, args):
        for t in args:
            if not self.fs.exists(t):
                self.fs.write(t, "")
        return ""

    def cmd_rm(self, args):
        targets = [a for a in args if not a.startswith("-")]
        for t in targets:
            if not self.fs.remove(t):
                return f"rm: cannot remove '{t}': No such file or directory"
        return ""

    def cmd_shutdown(self, args):
        self.exit_requested = True
        self.db.log("shutdown_attempt", service="ssh", src_ip=self.src_ip,
                    username=self.username)
        return "Broadcast message from root@web01: The system is going down for reboot NOW!"

    cmd_reboot = cmd_shutdown
    cmd_poweroff = cmd_shutdown

    def cmd_apt_get(self, args):
        return ("Reading package lists... Done\nBuilding dependency tree... Done\n"
                "0 upgraded, 0 newly installed, 0 to remove and 4 not upgraded.")

    cmd_apt = cmd_apt_get

    def cmd_systemctl(self, args):
        if args and args[0] == "status":
            unit = args[1] if len(args) > 1 else "ssh"
            return (f"* {unit}.service\n   Loaded: loaded (/lib/systemd/system/{unit}.service; enabled)\n"
                    "   Active: active (running) since Sun 2026-08-20 09:10:22 UTC; 3 days ago")
        return ""

    cmd_service = cmd_systemctl


def shlex_split(line):
    import shlex
    return shlex.split(line)


REDIRECT_RE = re.compile(r"^\d*\s*[<>]")


def tokenize(line):
    import shlex
    lexer = shlex.shlex(line, posix=True, punctuation_chars=";|&")
    lexer.whitespace_split = True
    tokens = []
    for token in lexer:
        if REDIRECT_RE.match(token):
            continue
        if token:
            tokens.append(token)
    return tokens


class PotInterface(paramiko.ServerInterface):
    def __init__(self, db, addr):
        self.db = db
        self.addr = addr
        self.username = None
        self.shell_event = threading.Event()

    def get_allowed_auths(self, username):
        return "password,publickey"

    def check_auth_none(self, username):
        self._log_auth(username, None, method="none")
        return paramiko.AUTH_FAILED

    def check_auth_publickey(self, username, key):
        self.db.log("auth", service="ssh", src_ip=self.addr[0], src_port=self.addr[1],
                    username=username, detail="publickey rejected",
                    extra={"key_type": key.get_name(), "fingerprint": key.get_fingerprint().hex()})
        return paramiko.AUTH_FAILED

    def check_auth_password(self, username, password):
        self._log_auth(username, password, method="password")
        if not password or password.lower() in BLACKLIST_PASSWORDS:
            return paramiko.AUTH_FAILED
        self.username = username
        self.db.log("login", service="ssh", src_ip=self.addr[0], src_port=self.addr[1],
                    username=username, password=password, detail="accepted")
        return paramiko.AUTH_SUCCESSFUL

    def _log_auth(self, username, password, method):
        self.db.log("auth", service="ssh", src_ip=self.addr[0], src_port=self.addr[1],
                    username=username, password=password, detail=f"method={method}")

    def check_channel_request(self, kind, chanid):
        if kind == "session":
            return paramiko.OPEN_SUCCEEDED
        return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_pty_request(self, channel, term, width, height,
                                  pixelwidth, pixelheight, modes):
        if isinstance(term, bytes):
            term = term.decode("utf-8", errors="replace")
        self.db.log("pty_request", service="ssh", src_ip=self.addr[0],
                    detail=f"term={term} {width}x{height}")
        return True

    def check_channel_shell_request(self, channel):
        self.shell_event.set()
        return True

    def check_channel_exec_request(self, channel, command):
        shell = FakeShell(self.db, self.username, self.addr[0])
        line = command.decode("utf-8", errors="replace") if isinstance(command, bytes) else command
        output = shell.execute(line)
        channel.send(output.encode())
        channel.send_exit_status(0)
        channel.close()
        return True

    def check_channel_subsystem_request(self, channel, name):
        self.db.log("subsystem_request", service="ssh", src_ip=self.addr[0],
                    src_port=self.addr[1], detail=name)
        return False

    def check_channel_window_change_request(self, channel, width, height,
                                           pixelwidth, pixelheight):
        return True


def run_interactive(chan, db, addr, username):
    shell = FakeShell(db, username, addr[0])
    last_login = datetime.now().strftime("%a %b %d %H:%M:%S %Y")
    motd = (
        f"Welcome to Ubuntu 22.04.3 LTS (GNU/Linux {FAKE_KERNEL} x86_64)\r\n\r\n"
        " * Documentation:  https://help.ubuntu.com\r\n"
        " * Management:     https://landscape.canonical.com\r\n"
        " * Support:        https://ubuntu.com/advantage\r\n\r\n"
        f"Last login: {last_login} from {addr[0]}\r\n"
    )
    chan.sendall(motd)
    chan.sendall(shell.prompt.encode())

    line = ""
    while shell.exit_requested is False:
        try:
            data = chan.recv(1024)
        except socket.timeout:
            db.log("disconnect", service="ssh", src_ip=addr[0], src_port=addr[1],
                   detail="idle timeout")
            break
        if not data:
            break
        for ch in data.decode("utf-8", errors="replace"):
            if ch in ("\r", "\n"):
                chan.sendall(b"\r\n")
                if line.strip():
                    shell.history.append(line)
                    output = shell.execute(line)
                    if output:
                        chan.sendall(output.replace("\n", "\r\n").encode() + b"\r\n")
                line = ""
                if shell.exit_requested:
                    chan.sendall(b"logout\r\nConnection to host closed.\r\n")
                    break
                chan.sendall(shell.prompt.encode())
            elif ch in ("\x7f", "\x08"):
                if line:
                    line = line[:-1]
                    chan.sendall(b"\b \b")
            elif ch == "\x03":
                chan.sendall(b"^C\r\n")
                line = ""
                chan.sendall(shell.prompt.encode())
            elif ch == "\x04":
                chan.sendall(b"\r\nexit\r\n")
                shell.exit_requested = True
                break
            elif ch == "\t":
                continue
            elif ord(ch) >= 32:
                line += ch
                chan.sendall(ch.encode())


def serve(db, stop_event):
    host_key = load_or_create_host_key(HOST_KEY_PATH)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.settimeout(1.0)
    sock.bind((BIND_HOST, SSH_PORT))
    sock.listen(128)
    while not stop_event.is_set():
        try:
            client, addr = sock.accept()
        except socket.timeout:
            continue
        threading.Thread(target=_handle_client,
                         args=(client, addr, db, host_key), daemon=True).start()
    sock.close()


def _handle_client(client, addr, db, host_key):
    db.log("connection", service="ssh", src_ip=addr[0], src_port=addr[1])
    transport = None
    try:
        client.settimeout(60)
        transport = paramiko.Transport(client)
        transport.local_version = SERVER_VERSION
        transport.add_server_key(host_key)
        interface = PotInterface(db, addr)
        transport.start_server(server=interface)
        chan = transport.accept(90)
        if chan is None:
            raise TimeoutError("no channel opened")
        interface.shell_event.wait(30)
        if interface.shell_event.is_set():
            chan.settimeout(600)
            run_interactive(chan, db, addr, interface.username)
        else:
            deadline = time.time() + 30
            while transport.is_active() and time.time() < deadline:
                time.sleep(0.5)
    except Exception as exc:
        db.log("error", service="ssh", src_ip=addr[0], src_port=addr[1], detail=str(exc))
    finally:
        db.log("disconnect", service="ssh", src_ip=addr[0], src_port=addr[1])
        if transport is not None:
            try:
                transport.close()
            except Exception:
                pass
