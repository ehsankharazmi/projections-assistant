"""Backup/restore, persistent storage, remote-readiness checks, and secret-leak scanning."""

from __future__ import annotations

import importlib.util
import logging
import os
from pathlib import Path

import pytest

from app.ops import backup, readiness
from tests.conftest import H, load_demo, run_scenario

ROOT = Path(__file__).resolve().parents[2]


def _load_checker():
    spec = importlib.util.spec_from_file_location("check_secrets", ROOT / "scripts" / "check_secrets.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_backup_restore_round_trip_and_app_restart(make_client, tmp_path):
    client = make_client()
    wb = load_demo(client)
    scenario = run_scenario(client, wb, 1.08)
    applied = client.post(f"/api/scenarios/{scenario['id']}/apply", headers={**H, "Idempotency-Key": "b"},
                          json={"expected_active_version_id": wb["active_version"]["id"]}).json()["version"]
    out = tmp_path / "backup1"
    manifest = backup.create(tmp_path / "data", out)  # while the app is running
    assert any(f["sha256"] == applied["sha256"] for f in manifest["files"])
    assert not any("env" in f["path"] for f in manifest["files"])
    client.__exit__(None, None, None)

    with pytest.raises(SystemExit):
        backup.restore(out, tmp_path / "data")  # refuses a non-empty target
    moved = backup.restore(out, tmp_path / "data", replace=True)  # in-place restore keeps old data aside
    assert moved is not None and (moved / "app.db").exists()
    restored_dir = tmp_path / "restored"
    backup.restore(out, restored_dir)
    restored = make_client(data_dir=restored_dir)
    info = restored.get(f"/api/workbooks/{wb['id']}").json()
    assert info["active_version"]["id"] == applied["id"]
    export = restored.get(f"/api/workbooks/{wb['id']}/export", params={"version_id": applied["id"]})
    assert export.status_code == 200 and export.headers["x-version-sha256"] == applied["sha256"]
    assert restored.get(f"/api/scenarios/{scenario['id']}").json()["status"] == "applied"


def test_backup_verification_detects_tampering(make_client, tmp_path):
    client = make_client()
    load_demo(client)
    out = tmp_path / "b"
    manifest = backup.create(tmp_path / "data", out)
    victim = out / "files" / manifest["files"][0]["path"]
    os.chmod(victim, 0o644)
    victim.write_bytes(b"tampered")
    with pytest.raises(SystemExit, match="verification failed"):
        backup.verify(out)


def _env(tmp_path: Path, text: str) -> Path:
    env = tmp_path / ".env"
    env.write_text(text)
    env.chmod(0o600)
    return env


def test_readiness_rejects_local_auth_and_unsafe_origin(tmp_path):
    env = _env(tmp_path, "DEPLOYMENT_PROFILE=local\nAUTH_MODE=local\nALLOWED_ORIGIN=http://127.0.0.1:8080\n")
    problems = readiness.check(env, tmp_path / "missing-token")
    text = " ".join(problems)
    assert "DEPLOYMENT_PROFILE" in text and "AUTH_MODE" in text and "ALLOWED_ORIGIN" in text
    assert "tunnel token" in text


def test_readiness_rejects_missing_access_settings_and_ai_key(tmp_path):
    env = _env(tmp_path, "DEPLOYMENT_PROFILE=remote\nAUTH_MODE=cloudflare_access\nAI_MODE=anthropic\n")
    problems = readiness.check(env, tmp_path / "t")
    assert any("invalid configuration" in p for p in problems)


def test_readiness_accepts_complete_remote_configuration(tmp_path):
    env = _env(tmp_path, "\n".join([
        "DEPLOYMENT_PROFILE=remote", "AUTH_MODE=cloudflare_access", "ALLOWED_ORIGIN=https://assistant.example.com",
        "CF_ACCESS_TEAM_DOMAIN=https://team.cloudflareaccess.com", "CF_ACCESS_AUDIENCE=aud",
        "ALLOWED_EMAILS=owner@example.com,tester@example.com", "OWNER_EMAIL=owner@example.com",
        "AI_MODE=anthropic", "ANTHROPIC_API_KEY=sk-ant-placeholder-for-test"]))
    token = tmp_path / "token"
    token.write_text("x")
    token.chmod(0o600)
    assert readiness.check(env, token) == []
    env.chmod(0o644)
    assert any("chmod 600" in p for p in readiness.check(env, token))


def test_secret_sentinel_absent_from_logs_and_frontend(make_client, caplog, tmp_path):
    from tests.test_ai import KEY, FakeClient, final, msg
    caplog.set_level(logging.DEBUG)
    client = make_client(ai_mode="anthropic", anthropic_api_key=KEY,
                         anthropic_client=FakeClient([msg([final()])]))
    wb = load_demo(client)
    client.post(f"/api/workbooks/{wb['id']}/messages", headers=H, json={"text": "hello"})
    log_file = tmp_path / "captured.log"
    log_file.write_text(caplog.text)
    checker = _load_checker()
    assert checker.scan([log_file, tmp_path / "data"], KEY) == []
    dist = ROOT / "frontend" / "dist"
    if dist.exists():
        assert checker.scan([dist], KEY) == []
    leak = tmp_path / "leak.js"
    leak.write_text(f"const k = '{KEY}'")
    assert checker.scan([leak], KEY)  # the scanner does detect a leak
