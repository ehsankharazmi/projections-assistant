"""Remote-readiness check: refuses configurations that are unsafe to expose through the tunnel.

    uv run python -m app.ops.readiness --env-file .env [--token-file secrets/cloudflare_tunnel_token]

Exit code 0 means ready. Nothing secret is printed.
"""

from __future__ import annotations

import argparse
import stat
import sys
from pathlib import Path

from pydantic import ValidationError

from app.config import Settings


def check(env_file: Path, token_file: Path) -> list[str]:
    problems: list[str] = []
    if not env_file.exists():
        return [f"{env_file} does not exist"]
    mode = env_file.stat().st_mode
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        problems.append(f"{env_file} is readable by other users; run: chmod 600 {env_file}")
    try:
        s = Settings(_env_file=str(env_file))
    except ValidationError as exc:
        return problems + [f"invalid configuration: {e['msg']}" for e in exc.errors()]
    if s.deployment_profile != "remote":
        problems.append("DEPLOYMENT_PROFILE must be 'remote' for shared access")
    if s.auth_mode != "cloudflare_access":
        problems.append("AUTH_MODE must be 'cloudflare_access'; the local developer identity is refused remotely")
    if not s.allowed_origin.startswith("https://"):
        problems.append("ALLOWED_ORIGIN must be the https:// address of the shared hostname")
    if "localhost" in s.allowed_origin or "127.0.0.1" in s.allowed_origin:
        problems.append("ALLOWED_ORIGIN must not be a localhost address in the remote profile")
    if not s.owner_email:
        problems.append("OWNER_EMAIL should name the owner (for owner-only pages)")
    elif s.owner_email.lower() not in s.allowed_email_set:
        problems.append("OWNER_EMAIL must be one of ALLOWED_EMAILS")
    if len(s.allowed_email_set) > 5:
        problems.append("ALLOWED_EMAILS lists more than 5 addresses; this prototype is for an owner and one tester")
    if s.ai_mode == "anthropic":
        if s.ai_daily_budget_usd <= 0 or s.ai_monthly_budget_usd <= 0:
            problems.append("set positive AI_DAILY_BUDGET_USD and AI_MONTHLY_BUDGET_USD")
        if not s.anthropic_api_key.get_secret_value().startswith("sk-ant-"):
            problems.append("ANTHROPIC_API_KEY does not look like an Anthropic API key")
    if not token_file.exists():
        problems.append(f"tunnel token file {token_file} is missing")
    elif token_file.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        problems.append(f"{token_file} is readable by other users; run: chmod 600 {token_file}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--token-file", type=Path, default=Path("secrets/cloudflare_tunnel_token"))
    args = parser.parse_args(argv)
    problems = check(args.env_file, args.token_file)
    if problems:
        print("NOT READY for remote access:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("Ready: remote profile configuration passed all checks.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
