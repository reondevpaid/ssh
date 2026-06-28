#!/usr/bin/env python3
# Made for IamGunpoint
"""
IamGunpoint's HopX SSH Terminal
Simple terminal-based HopX sandbox manager.

Install:
  pip install hopx-ai

Run:
  python3 app.py

First run:
  - checks ~/.hopx_ssh/config.json
  - if no API key, asks for it
  - saves it

Menu:
  1) create
  2) stop
  3) start
  4) delete
  5) terminal
  6) exit
  plus extra useful options.

Notes:
  - "max timeout" is not published as one exact number in the quickstart docs.
    This script tries a very large timeout first, then falls back through smaller
    values until HopX accepts one.
  - This is SSH-like, not real OpenSSH. It runs shell commands through HopX SDK.
  - Commands that require a true interactive TTY, like `sudo su`, may not become
    an interactive root shell through the command API. The syntax wrapper is fixed,
    but HopX command execution is still non-PTY command execution.
"""

from __future__ import annotations

import getpass
import json
import os
import shlex
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

try:
    from hopx_ai import Sandbox
except Exception:
    Sandbox = None

try:
    from hopx_ai.exceptions import APIError, ResourceLimitError
except Exception:
    APIError = ResourceLimitError = Exception

APP_DIR = Path.home() / ".hopx_ssh"
CONFIG_FILE = APP_DIR / "config.json"
DEFAULT_TEMPLATE = "code-interpreter"
DEFAULT_CWD = "/workspace"
OWNER_NAME = "IamGunpoint"

# HopX docs do not state one exact maximum timeout in the quickstart.
# So "max" means: try these from largest to smaller until HopX accepts.
MAX_TIMEOUT_TRIES = [
    2_147_483_647,  # int32 max seconds, ~68 years; likely rejected, but tried first
    315_360_000,    # 10 years
    31_536_000,     # 1 year
    2_592_000,      # 30 days
    604_800,        # 7 days
    172_800,        # 48 hours
    86_400,         # 24 hours
    43_200,         # 12 hours
    21_600,         # 6 hours
    7_200,          # 2 hours
    3_600,          # 1 hour
]


# ---------- tiny colors ----------
class C:
    reset = "\033[0m"
    bold = "\033[1m"
    dim = "\033[2m"
    red = "\033[91m"
    green = "\033[92m"
    yellow = "\033[93m"
    blue = "\033[94m"
    magenta = "\033[95m"
    cyan = "\033[96m"


def color(text: str, c: str) -> str:
    if os.environ.get("NO_COLOR"):
        return text
    return c + text + C.reset


def clear() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def pause() -> None:
    input(color("\npress enter...", C.dim))


def banner() -> None:
    clear()
    art = f"""
{C.cyan}{C.bold}╔══════════════════════════════════════════════════════════════╗
║               H O P X   S S H   T E R M I N A L            ║
║                       by {OWNER_NAME:<34}║
╚══════════════════════════════════════════════════════════════╝{C.reset}
""".rstrip()
    print(art)
    print(color("simple · fast · sandbox terminal · no web panel\n", C.dim))


def ok(msg: str) -> None:
    print(color("✓ ", C.green) + msg)


def warn(msg: str) -> None:
    print(color("! ", C.yellow) + msg)


def bad(msg: str) -> None:
    print(color("✗ ", C.red) + msg)


def info(msg: str) -> None:
    print(color("› ", C.cyan) + msg)


# ---------- config ----------
def load_config() -> Dict[str, Any]:
    try:
        return json.loads(CONFIG_FILE.read_text())
    except Exception:
        return {}


def save_config(cfg: Dict[str, Any]) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))
    try:
        CONFIG_FILE.chmod(0o600)
    except Exception:
        pass


def require_sdk() -> None:
    if Sandbox is not None:
        return
    bad("hopx-ai SDK is not installed")
    print("\nInstall it:")
    print(color("  pip install hopx-ai", C.cyan))
    sys.exit(2)


def setup_api_key() -> str:
    cfg = load_config()
    if cfg.get("api_key"):
        return str(cfg["api_key"])
    if os.environ.get("HOPX_API_KEY"):
        cfg["api_key"] = os.environ["HOPX_API_KEY"]
        save_config(cfg)
        return cfg["api_key"]

    banner()
    warn("No API key found in ~/.hopx_ssh/config.json")
    print("Paste your HopX API key. It will be saved locally.")
    print(color("Tip: you can also set HOPX_API_KEY env var.\n", C.dim))
    key = getpass.getpass("HopX API key: ").strip()
    if not key:
        bad("API key required")
        sys.exit(1)
    cfg["api_key"] = key
    save_config(cfg)
    ok("API key saved")
    time.sleep(0.7)
    return key


def set_current(sandbox_id: str) -> None:
    cfg = load_config()
    cfg["current_sandbox"] = sandbox_id
    cfg.setdefault("cwd", {})[sandbox_id] = cfg.setdefault("cwd", {}).get(sandbox_id, DEFAULT_CWD)
    save_config(cfg)


def get_current() -> str:
    return str(load_config().get("current_sandbox", ""))


def get_cwd(sandbox_id: str) -> str:
    return str(load_config().get("cwd", {}).get(sandbox_id, DEFAULT_CWD))


def set_cwd(sandbox_id: str, cwd: str) -> None:
    cfg = load_config()
    cfg.setdefault("cwd", {})[sandbox_id] = cwd
    save_config(cfg)


def reset_api_key() -> None:
    cfg = load_config()
    cfg.pop("api_key", None)
    save_config(cfg)
    warn("API key removed. Restart script to login again.")


# ---------- HopX helpers ----------
def sid_of(sb: Any) -> str:
    return str(getattr(sb, "sandbox_id", None) or getattr(sb, "id", None) or "unknown")


def val(obj: Any, key: str, default: Any = "") -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def connect(api_key: str, sandbox_id: Optional[str] = None) -> Any:
    require_sdk()
    sid = sandbox_id or get_current()
    if not sid:
        sid = input("Sandbox ID: ").strip()
    if not sid:
        raise RuntimeError("No sandbox selected")
    sb = Sandbox.connect(sid, api_key=api_key)
    set_current(sid_of(sb))
    return sb


def parse_timeout_choice(raw: str) -> list[int]:
    raw = raw.strip().lower()
    if raw in {"", "max", "m", "maximum"}:
        return MAX_TIMEOUT_TRIES[:]
    try:
        seconds = int(raw)
        return [seconds]
    except Exception:
        warn("Invalid timeout, using max fallback list")
        return MAX_TIMEOUT_TRIES[:]


def create(api_key: str) -> Any:
    require_sdk()
    template = input(f"Template [{DEFAULT_TEMPLATE}]: ").strip() or DEFAULT_TEMPLATE
    timeout_raw = input("Timeout seconds [max]: ").strip()
    timeout_tries = parse_timeout_choice(timeout_raw)

    last_error: Optional[Exception] = None
    for timeout in timeout_tries:
        try:
            info(f"creating sandbox template={template}, timeout={timeout}s ...")
            sb = Sandbox.create(template=template, timeout_seconds=timeout, api_key=api_key)
            set_current(sid_of(sb))
            ok(f"created {sid_of(sb)}")
            ok(f"accepted timeout: {timeout}s")
            show_info(sb)
            return sb
        except Exception as e:
            last_error = e
            warn(f"timeout {timeout}s rejected/failed: {e}")
            time.sleep(0.2)

    raise RuntimeError(f"Could not create sandbox with any timeout. Last error: {last_error}")


def list_sandboxes(api_key: str) -> list[Any]:
    require_sdk()
    info("fetching sandboxes...")
    boxes = list(Sandbox.list(api_key=api_key, limit=100))
    if not boxes:
        warn("No sandboxes found")
        return []
    print()
    print(color("#   Sandbox ID                         Status       Template", C.bold))
    print(color("─" * 70, C.dim))
    for i, sb in enumerate(boxes, 1):
        try:
            inf = sb.get_info()
        except Exception:
            inf = {}
        sid = sid_of(sb)
        status = str(val(inf, "status", "?")).ljust(12)
        template = str(val(inf, "template_name", ""))
        mark = "*" if sid == get_current() else " "
        print(f"{mark}{i:<3} {sid:<34} {status} {template}")
    print()
    return boxes


def choose_sandbox(api_key: str) -> Optional[str]:
    boxes = list_sandboxes(api_key)
    if not boxes:
        return None
    choice = input("Choose # or paste sandbox id: ").strip()
    if not choice:
        return None
    if choice.isdigit():
        idx = int(choice) - 1
        if 0 <= idx < len(boxes):
            sid = sid_of(boxes[idx])
            set_current(sid)
            ok(f"selected {sid}")
            return sid
    set_current(choice)
    ok(f"selected {choice}")
    return choice


def show_info(sb: Any) -> None:
    try:
        inf = sb.get_info()
    except Exception as e:
        bad(f"could not get info: {e}")
        return
    print()
    print(color("Sandbox Info", C.bold + C.cyan))
    print(color("─" * 50, C.dim))
    for k in ["sandbox_id", "id", "status", "template_name", "region", "public_host", "direct_url", "created_at", "expires_at", "timeout_seconds"]:
        v = val(inf, k, None)
        if v:
            print(f"{k:14}: {v}")
    print()


def action(api_key: str, name: str) -> None:
    sb = connect(api_key)
    sid = sid_of(sb)
    if name == "start":
        info(f"starting {sid}...")
        sb.start()
        ok("started")
    elif name == "stop":
        info(f"stopping {sid}...")
        sb.stop()
        ok("stopped")
    elif name == "delete":
        confirm = input(color(f"Delete {sid} permanently? type DELETE: ", C.red)).strip()
        if confirm != "DELETE":
            warn("cancelled")
            return
        sb.kill()
        ok("deleted")
        cfg = load_config()
        if cfg.get("current_sandbox") == sid:
            cfg.pop("current_sandbox", None)
        cfg.get("cwd", {}).pop(sid, None)
        save_config(cfg)
    elif name == "pause":
        sb.pause()
        ok("paused")
    elif name == "resume":
        sb.resume()
        ok("resumed")


# ---------- terminal ----------
def run_command(sb: Any, command: str, cwd: str, timeout: int = 300) -> Tuple[str, str, int, str]:
    """
    Run command in HopX sandbox and preserve cwd.

    Important fix:
      This wrapper avoids the old brace/semicolon syntax issue. It runs the
      command, captures `$?`, prints cwd, then exits with the real command code.
    """
    marker = "__HOPX_SSH_CWD__"
    safe_cwd = shlex.quote(cwd)

    wrapped = (
        f"cd {safe_cwd} 2>/dev/null || cd /workspace 2>/dev/null || cd /\n"
        f"{command}\n"
        f"__hopx_ssh_code=$?\n"
        f"printf '\\n{marker}:%s\\n' \"$PWD\"\n"
        f"exit $__hopx_ssh_code\n"
    )

    res = sb.commands.run(wrapped, timeout=timeout, working_dir="/")
    stdout = str(getattr(res, "stdout", "") or "")
    stderr = str(getattr(res, "stderr", "") or "")
    code_raw = getattr(res, "exit_code", 0)
    code = int(code_raw if code_raw is not None else 0)
    new_cwd = cwd

    if marker + ":" in stdout:
        before, _, after = stdout.rpartition(marker + ":")
        stdout = before.rstrip("\n") + ("\n" if before.rstrip("\n") else "")
        new_cwd = after.splitlines()[0].strip() or cwd

    return stdout, stderr, code, new_cwd


def terminal_help() -> None:
    print(color("""
Terminal commands:
  help / :help              show this
  exit / :exit              exit terminal, keep sandbox running
  clear                     clear screen
  info                      sandbox info
  files [path]              list remote files
  cat <file>                read remote file
  upload <local> <remote>   upload text file
  download <remote> <local> download text file
  py                        paste Python code, end with EOF
  preview [port]            show public preview URL
  delete                    delete sandbox and exit

Anything else runs as shell command in the sandbox.
Examples:
  pwd
  ls -la
  cd /workspace
  pip install requests
  python --version

Note:
  sudo su may not become an interactive root shell because HopX command execution
  is not a full PTY/OpenSSH session. Try direct commands like:
    whoami
    sudo whoami
    sudo apt update
""".strip(), C.cyan))


def paste_until_eof(language: str) -> str:
    print(f"Paste {language} code. End with a single line: EOF")
    lines = []
    while True:
        line = input()
        if line.strip() == "EOF":
            break
        lines.append(line)
    return "\n".join(lines)


def preview_url(sb: Any, port: int) -> str:
    if hasattr(sb, "get_preview_url"):
        try:
            return str(sb.get_preview_url(port=port))
        except Exception:
            pass
    try:
        inf = sb.get_info()
        host = str(val(inf, "public_host", val(inf, "direct_url", ""))).rstrip("/")
        if not host:
            return ""
        if "://" in host:
            scheme, rest = host.split("://", 1)
        else:
            scheme, rest = "https", host
        return f"{scheme}://{port}-{rest}"
    except Exception:
        return ""


def terminal(api_key: str) -> None:
    sb = connect(api_key)
    sid = sid_of(sb)
    cwd = get_cwd(sid)
    clear()
    ok(f"connected terminal: {sid}")
    print(color("type help for terminal commands\n", C.dim))

    while True:
        try:
            cmd = input(color(f"{sid[:10]}:{cwd}$ ", C.cyan + C.bold))
        except (KeyboardInterrupt, EOFError):
            print()
            warn("terminal closed; sandbox still running")
            return

        raw = cmd
        cmd = cmd.strip()
        if not cmd:
            continue
        try:
            if cmd in {"exit", ":exit", "quit"}:
                warn("terminal closed; sandbox still running")
                return
            if cmd in {"help", ":help"}:
                terminal_help()
                continue
            if cmd == "clear":
                clear()
                continue
            if cmd == "info":
                show_info(sb)
                continue
            if cmd.startswith("files"):
                parts = shlex.split(cmd)
                path = parts[1] if len(parts) > 1 else cwd
                files = sb.files.list(path)
                for f in files:
                    name = val(f, "name", str(f))
                    size = val(f, "size", "?")
                    print(f"- {name} ({size} bytes)")
                continue
            if cmd.startswith("cat "):
                path = shlex.split(cmd)[1]
                print(sb.files.read(path))
                continue
            if cmd.startswith("upload "):
                _, local, remote = shlex.split(cmd)
                data = Path(local).read_text(errors="replace")
                sb.files.write(remote, data)
                ok(f"uploaded {local} -> {remote}")
                continue
            if cmd.startswith("download "):
                _, remote, local = shlex.split(cmd)
                data = sb.files.read(remote)
                Path(local).write_text(str(data))
                ok(f"downloaded {remote} -> {local}")
                continue
            if cmd == "py":
                code = paste_until_eof("Python")
                res = sb.run_code(code, language="python", working_dir=cwd, timeout=300)
                out = getattr(res, "stdout", "") or ""
                err = getattr(res, "stderr", "") or ""
                if out:
                    print(out.rstrip())
                if err:
                    print(color(err.rstrip(), C.red))
                continue
            if cmd.startswith("preview"):
                parts = shlex.split(cmd)
                port = int(parts[1]) if len(parts) > 1 else 8000
                url = preview_url(sb, port)
                print(color(url or "no preview URL available", C.green))
                continue
            if cmd == "delete":
                confirm = input(color("Delete sandbox permanently? type DELETE: ", C.red)).strip()
                if confirm == "DELETE":
                    sb.kill()
                    ok("deleted")
                    return
                warn("cancelled")
                continue

            start = time.time()
            stdout, stderr, code, cwd = run_command(sb, raw, cwd)
            set_cwd(sid, cwd)
            if stdout:
                print(stdout.rstrip("\n"))
            if stderr:
                print(color(stderr.rstrip("\n"), C.red), file=sys.stderr)
            took = time.time() - start
            if code == 0:
                print(color(f"exit 0 · {took:.2f}s", C.dim))
            else:
                print(color(f"exit {code} · {took:.2f}s", C.red))
        except Exception as e:
            bad(str(e))


# ---------- menu ----------
def menu(api_key: str) -> None:
    while True:
        banner()
        current = get_current()
        print(color(f"Current sandbox: {current or 'none selected'}", C.bold))
        print()
        print("1) create")
        print("2) stop")
        print("3) start")
        print("4) delete")
        print("5) terminal")
        print("6) exit")
        print(color("\nMore:", C.dim))
        print("7) list/select sandboxes")
        print("8) info")
        print("9) pause")
        print("10) resume")
        print("11) change API key")
        print()
        choice = input(color("Choose: ", C.cyan)).strip().lower()
        try:
            if choice == "1":
                create(api_key)
                pause()
            elif choice == "2":
                action(api_key, "stop")
                pause()
            elif choice == "3":
                action(api_key, "start")
                pause()
            elif choice == "4":
                action(api_key, "delete")
                pause()
            elif choice == "5":
                terminal(api_key)
                pause()
            elif choice == "6" or choice in {"exit", "q", "quit"}:
                ok(f"bye {OWNER_NAME}")
                return
            elif choice == "7":
                choose_sandbox(api_key)
                pause()
            elif choice == "8":
                show_info(connect(api_key))
                pause()
            elif choice == "9":
                action(api_key, "pause")
                pause()
            elif choice == "10":
                action(api_key, "resume")
                pause()
            elif choice == "11":
                reset_api_key()
                return
            else:
                warn("invalid choice")
                time.sleep(0.8)
        except ResourceLimitError as e:
            bad(f"Resource limit: {e}")
            pause()
        except APIError as e:
            bad(f"API error: {e}")
            pause()
        except Exception as e:
            bad(str(e))
            pause()


def main() -> None:
    require_sdk()
    api_key = setup_api_key()
    menu(api_key)


if __name__ == "__main__":
    main()
