"""Scan built frontend assets and log files for a synthetic secret sentinel or Anthropic key patterns.

    python scripts/check_secrets.py --sentinel sk-ant-SENTINEL-xyz frontend/dist data/logs

Use it after building and running with ANTHROPIC_API_KEY set to the sentinel value:
any hit means a secret leaked into browser code or logs. Exit code 1 on findings.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

KEY_PATTERN = re.compile(rb"sk-ant-[A-Za-z0-9_\-]{8,}")


def scan(paths: list[Path], sentinel: str) -> list[str]:
    findings = []
    needle = sentinel.encode()
    for root in paths:
        files = [root] if root.is_file() else [p for p in root.rglob("*") if p.is_file()] if root.exists() else []
        for path in files:
            data = path.read_bytes()
            if needle in data:
                findings.append(f"{path}: contains the sentinel")
            elif KEY_PATTERN.search(data):
                findings.append(f"{path}: contains something that looks like an Anthropic API key")
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sentinel", required=True)
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args(argv)
    findings = scan(args.paths, args.sentinel)
    for f in findings:
        print(f)
    print("no secrets found" if not findings else f"{len(findings)} finding(s)")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
