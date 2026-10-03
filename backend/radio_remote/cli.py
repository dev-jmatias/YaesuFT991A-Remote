"""Local administration for a locked-out or headless install. Run ON the Pi, as the service user:

    python -m radio_remote.cli list-users
    python -m radio_remote.cli reset-password alice
    python -m radio_remote.cli create-admin bob

It talks to the database directly, so it needs filesystem access to the data directory; that access is the
authority (anyone who can read the database file could already do far worse). Passwords are prompted for,
never taken from the command line (they would end up in shell history).
"""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

from . import config
from .auth import AuthStore


def _store(args) -> AuthStore:
    cfg = config.load(args.config)
    return AuthStore(Path(cfg["storage"]["data_dir"]) / "radio-remote.db")


def _ask(prompt_confirm: bool = True) -> str:
    pw = getpass.getpass("New password (min 10 characters): ")
    if prompt_confirm and getpass.getpass("Repeat: ") != pw:
        raise SystemExit("passwords do not match")
    return pw


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="radio_remote.cli")
    ap.add_argument("--config", default="config/radio-remote.toml")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list-users")
    for name in ("reset-password", "create-admin"):
        sub.add_parser(name).add_argument("username")
    args = ap.parse_args(argv)
    auth = _store(args)

    if args.cmd == "list-users":
        for u in auth.list_users():
            print(f"{u['id']:>3}  {u['username']:<24} {u['role']}")
        return 0
    try:
        if args.cmd == "reset-password":
            user = next((u for u in auth.list_users() if u["username"].lower() == args.username.lower()), None)
            if not user:
                print(f"no such user: {args.username}", file=sys.stderr)
                return 1
            auth.set_password(user["id"], _ask())          # also signs the user out everywhere
            auth.audit("password_reset_cli", user["username"], "local")
        else:
            auth.create_user(args.username, _ask(), "admin")
            auth.audit("admin_created_cli", args.username, "local")
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
