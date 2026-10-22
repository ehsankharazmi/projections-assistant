"""Consistent backup and verified restore.

    python -m app.ops.backup create  --data-dir /data --out /data/backups/<name>
    python -m app.ops.backup verify  --backup /data/backups/<name>
    python -m app.ops.backup restore --backup /backups/<name> --data-dir /data [--replace]

The database is copied with SQLite's online backup API (a consistent snapshot
even while the app runs). Every workbook file referenced by that snapshot is
copied and listed in a manifest with its SHA-256; versions are immutable, so the
files match the snapshot. Secrets are not part of a backup: .env and tunnel
tokens live outside the data directory and must be recreated on restore.
Restore refuses a non-empty target unless --replace is given, in which case the
current contents are moved (not deleted) into <data-dir>/pre-restore-<timestamp>/.
Every hash is verified before and after copying. Stop the application first.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import sqlite3
import sys
from pathlib import Path

from app.storage import sha256_file

MANIFEST = "manifest.json"


def _referenced_files(db_path: Path) -> list[tuple[str, str | None]]:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows: list[tuple[str, str | None]] = []
        rows += con.execute("SELECT file_path, sha256 FROM workbook_versions").fetchall()
        rows += [(r[0], None) for r in con.execute("SELECT DISTINCT values_file_path FROM workbook_versions")]
        rows += con.execute("SELECT candidate_file_path, candidate_sha256 FROM scenarios "
                            "WHERE candidate_file_path IS NOT NULL").fetchall()
        rows += [(f"workbooks/{r[0]}/original.xlsx", r[1]) for r in
                 con.execute("SELECT id, original_sha256 FROM workbooks")]
    finally:
        con.close()
    seen: dict[str, str | None] = {}
    for path, digest in rows:
        if path and (path not in seen or seen[path] is None):
            seen[path] = digest
    return sorted(seen.items())


def create(data_dir: Path, out: Path) -> dict:
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"refusing to write into non-empty {out}")
    out.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(data_dir / "app.db")
    dst = sqlite3.connect(out / "app.db")
    with dst:
        src.backup(dst)
    src.close()
    dst.close()
    files = []
    for rel, recorded in _referenced_files(out / "app.db"):
        source = data_dir / rel
        if not source.exists():
            raise SystemExit(f"referenced file is missing: {rel}")
        digest = sha256_file(source)
        if recorded and recorded != digest:
            raise SystemExit(f"file does not match its recorded hash: {rel}")
        target = out / "files" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        files.append({"path": rel, "sha256": digest})
    manifest = {"created_at": dt.datetime.now(dt.UTC).isoformat(), "database": "app.db",
                "database_sha256": sha256_file(out / "app.db"), "files": files,
                "note": "Secrets (.env, tunnel token) are intentionally not included."}
    (out / MANIFEST).write_text(json.dumps(manifest, indent=2))
    verify(out)
    return manifest


def verify(backup: Path) -> dict:
    manifest = json.loads((backup / MANIFEST).read_text())
    problems = []
    if sha256_file(backup / manifest["database"]) != manifest["database_sha256"]:
        problems.append("database hash mismatch")
    con = sqlite3.connect(backup / manifest["database"])
    if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        problems.append("database integrity check failed")
    con.close()
    for entry in manifest["files"]:
        path = backup / "files" / entry["path"]
        if not path.exists() or sha256_file(path) != entry["sha256"]:
            problems.append(f"file missing or changed: {entry['path']}")
    if problems:
        raise SystemExit("backup verification failed: " + "; ".join(problems))
    return manifest


def restore(backup: Path, data_dir: Path, *, replace: bool = False) -> Path | None:
    manifest = verify(backup)
    existing = [p for p in data_dir.iterdir() if p.name != "jobs" and not p.name.startswith("pre-restore-")] \
        if data_dir.exists() else []
    moved_to = None
    if existing and not replace:
        raise SystemExit(f"refusing to restore into non-empty {data_dir}; stop the app and pass --replace "
                         "(current data is moved aside, not deleted) or use an empty directory")
    if existing:
        moved_to = data_dir / f"pre-restore-{dt.datetime.now(dt.UTC).strftime('%Y%m%dT%H%M%SZ')}"
        moved_to.mkdir()
        for p in existing:
            shutil.move(str(p), moved_to / p.name)
    data_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(backup / manifest["database"], data_dir / "app.db")
    for entry in manifest["files"]:
        target = data_dir / entry["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(backup / "files" / entry["path"], target)
        if sha256_file(target) != entry["sha256"]:
            raise SystemExit(f"restored file does not match manifest: {entry['path']}")
    (data_dir / "jobs").mkdir(exist_ok=True)
    return moved_to


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create")
    c.add_argument("--data-dir", type=Path, default=Path("/data"))
    c.add_argument("--out", type=Path, required=True)
    v = sub.add_parser("verify")
    v.add_argument("--backup", type=Path, required=True)
    r = sub.add_parser("restore")
    r.add_argument("--backup", type=Path, required=True)
    r.add_argument("--data-dir", type=Path, required=True)
    r.add_argument("--replace", action="store_true", help="move current data aside instead of refusing")
    args = parser.parse_args(argv)
    if args.cmd == "create":
        m = create(args.data_dir, args.out)
        print(f"backup written to {args.out}: database + {len(m['files'])} files, verified")
    elif args.cmd == "verify":
        m = verify(args.backup)
        print(f"backup OK: database + {len(m['files'])} files")
    else:
        moved = restore(args.backup, args.data_dir, replace=args.replace)
        if moved:
            print(f"previous data moved to {moved}")
        print(f"restored into {args.data_dir}; recreate secrets (.env, tunnel token) before starting")


if __name__ == "__main__":
    sys.exit(main())
