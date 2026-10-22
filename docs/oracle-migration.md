# Optional: moving to an Oracle Cloud VM (Phase C)

This runs the same containers on a Linux VM, so the app stays available while your computer is off. Nothing here is provisioned automatically. Follow it yourself when you're ready, under your own Oracle account.

## Before you choose an instance

- Check **your account's current Always Free quotas and regional availability**: <https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm>. Free-tier allocations change and capacity is often unavailable in some regions. Don't rely on figures from older guides, and don't assume uptime or capacity.
- **Ampere (Arm64)** shapes need Arm64 images. All the base images used here are multi-architecture, and building on the VM itself (`docker compose build`) produces Arm64 images. Arm64 builds were not tested for this prototype.
- Plan for at least 2 GB RAM: LibreOffice is capped at 1 GiB in the worker, plus the backend and the gateway.
- **Free-tier reclamation:** Oracle may reclaim idle Always Free compute. Keep regular off-VM backups (below), and check Oracle's current policy.

## Set up the VM

1. Create an Ubuntu LTS VM. Allow **only SSH** inbound in the security list, and restrict it to your IP if possible.
   Do **not** open 80, 443 or 8080: the Cloudflare tunnel makes outbound connections only.
2. Install Docker: `sudo apt install docker.io docker-compose-v2 git` and add your user to the `docker` group.
3. Clone the repository and recreate the secrets on the VM. Never copy them inside backups.
   - **`.env`** (`chmod 600`): ideally create a **new** Anthropic key for the VM, a service-account key in the same workspace, and revoke the old one later.
   - **`secrets/cloudflare_tunnel_token`** (`chmod 600`).

## Move the data

1. On your computer, stop changes. Either stop sharing, or stop the gateway with `docker compose stop gateway`.
2. Create and copy a backup (see [backup-restore.md](backup-restore.md)):
   ```bash
   docker compose exec backend python -m app.ops.backup create --data-dir /data --out /backups/migrate
   docker compose cp backend:/backups/migrate ./backups/migrate
   scp -r ./backups/migrate vm:~/projections-assistant/backups/
   ```
3. On the VM, verify the backup, start the stack once so the volumes exist, then restore:
   ```bash
   uv run python -m app.ops.backup verify --backup backups/migrate    # or run it inside the backend container
   docker compose up -d --build && docker compose stop gateway backend worker
   docker compose cp backups/migrate backend:/backups/migrate
   docker compose run --rm --no-deps backend python -m app.ops.backup restore --backup /backups/migrate --data-dir /data --replace
   docker compose up -d
   ```
4. **Test before switching the hostname.** Use `docker compose exec backend ...` or an SSH tunnel to `127.0.0.1:8080`, and check:
   - a demo or real workbook recalculation works,
   - a version export matches its hash,
   - after a restart (`docker compose restart`), the history and conversations are still there,
   - `/api/health` shows the worker as `ok`.

## Switch the hostname

Choose one of these:
- Run the **same tunnel** on the VM by starting `cloudflared` with the same token there. Then **stop it on your computer**: two connectors on one tunnel would split traffic between two diverging copies.
- Create a new tunnel for the VM and move the public hostname to it.

Then run the readiness check on the VM and repeat the remote tests in [remote-access.md](remote-access.md#6-start-and-verify-from-another-device): login, refusal of non-listed emails, export, and the protection of the API.

Finally, **stop the old instance** on your computer (`docker compose --profile remote down`, *without* `-v`) so you don't end up with two diverging copies. Keep its data until you're confident in the VM.

## Operating the VM

| Task | How |
|---|---|
| OS updates | `sudo apt update && sudo apt upgrade`, then reboot when the kernel changes |
| App updates | Back up first, then `git pull && docker compose up -d --build` |
| Backups | A scheduled `app.ops.backup create`, copied **off the VM** (e.g. `scp` to your computer or object storage) |
| Monitoring | `docker compose ps`, `/api/health`, `df -h` (disk) and `free -h` (memory). Check the Claude Console usage page. |
| Rollback | Restore the pre-upgrade backup with `--replace`, or check out the previous git tag and rebuild |
| Retiring | Stop the stack, take a final backup and keep it, then terminate the VM in the Oracle console |
