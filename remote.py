#!/usr/bin/env python3
"""
Start Claude Code Remote Control (`claude rc`) in this project, so you can keep working on
Tunebox from claude.ai/code or the Claude mobile app.

    python remote.py                     # session named "tunebox"
    python remote.py --name bugfixes     # your own session name
    python remote.py -c                  # reattach to the last session started here
    python remote.py --spawn session     # any other `claude rc` option is passed through
    python remote.py --help              # all options

Needs the `claude` CLI on your PATH and a Claude account with a subscription.
Stop it with Ctrl+C.
"""
import os
import shutil
import subprocess
import sys

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_NAME = "tunebox"

# Options that already choose which session to use; adding a default --name next to them would be wrong.
_SESSION_CHOICE = ("--name", "-c", "--continue", "--session-id", "-h", "--help")


def build_command(claude: str, args: list) -> list:
    """`claude rc` plus the user's options, with a default session name unless they chose one."""
    chosen = any(a == opt or a.startswith(opt + "=") for a in args for opt in _SESSION_CHOICE)
    return [claude, "rc"] + ([] if chosen else ["--name", DEFAULT_NAME]) + args


def main() -> int:
    claude = shutil.which("claude")
    if not claude:
        print("Couldn't find the `claude` command on your PATH.\n"
              "Install Claude Code (https://claude.com/claude-code), open a new terminal, and try again.",
              file=sys.stderr)
        return 1

    cmd = build_command(claude, sys.argv[1:])
    print(f"Starting Remote Control in {PROJECT_DIR}\n  {' '.join(cmd[1:])}\n", flush=True)
    try:
        # Inherit this terminal so `claude rc` can show its QR code / link and read key presses.
        return subprocess.call(cmd, cwd=PROJECT_DIR)
    except KeyboardInterrupt:
        return 130          # Ctrl+C already reached `claude rc`; just exit quietly


if __name__ == "__main__":
    sys.exit(main())
