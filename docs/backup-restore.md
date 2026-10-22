# Backup and restore

A backup set contains:

- **`app.db`:** a consistent snapshot of the SQLite database, taken with SQLite's online backup API. This is safe while the app is running. Never copy the live `app.db` file by hand.
- **`files/`:** every workbook file the snapshot references: original uploads, versions, recalculated baselines and previews. Versions are immutable, so the files always match the snapshot.
- **`manifest.json`:** the SHA-256 of the database and of every file. Each file is checked against the hash recorded in the database while the backup is being made.

**Secrets are not included.** Recreate `.env` and `secrets/cloudflare_tunnel_token` on the destination, and rotate them if a backup may have been exposed.

## Create a backup

```bash
NAME=$(date -u +%Y%m%dT%H%M%SZ)
docker compose exec backend python -m app.ops.backup create --data-dir /data --out /backups/$NAME
docker compose cp backend:/backups/$NAME ./backups/$NAME     # copy off the Docker volume (git-ignored folder)
```

The command verifies the set after writing it. To check a copied set again later:

```bash
uv run python -m app.ops.backup verify --backup ./backups/$NAME
```

Keep at least one copy on another disk or machine.

## Restore

Restore needs the application stopped, so nothing writes during the restore.

```bash
docker compose stop gateway backend worker
docker compose cp ./backups/$NAME backend:/backups/$NAME           # if the set is not already in the volume
docker compose run --rm --no-deps backend \
    python -m app.ops.backup restore --backup /backups/$NAME --data-dir /data --replace
docker compose up -d
```

- `--replace` moves the current data into `/data/pre-restore-<timestamp>/` instead of deleting it.
- Without `--replace`, restore refuses to run on a non-empty directory.
- Every hash is verified before and after copying.

After restoring:
1. Open the app and check that the workbooks, versions and conversations are there.
2. Export a version and compare its `X-Version-Sha256` header, or the history panel, with the manifest.
3. Once you're satisfied, delete the `pre-restore-*` folder.

## Upgrades

```bash
docker compose exec backend python -m app.ops.backup create --data-dir /data --out /backups/pre-upgrade-$(date -u +%Y%m%d)
git pull
docker compose up -d --build        # database migrations run automatically at backend start
```

Never use `docker compose down -v` during an upgrade: it deletes the data volumes.

## What was tested

`backend/tests/test_ops.py` checks the following automatically:
- creating a backup while the app is running,
- refusal to restore into non-empty data without `--replace`,
- an in-place `--replace` restore,
- a restore into a fresh directory followed by an application restart that serves the same versions with identical hashes,
- detection of a tampered file.

The `docker compose` command lines above are documented but were not executed by the test suite.
