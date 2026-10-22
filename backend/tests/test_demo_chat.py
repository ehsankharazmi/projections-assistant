"""Scripted demo conversation: acceptance checks 2 (clarify first), 3 (revise without compounding), 4 (explain)."""

from __future__ import annotations

import pytest

from app.demo.model import compute, scenario_inputs
from tests.conftest import H, load_demo


def say(client, wb, text):
    r = client.post(f"/api/workbooks/{wb['id']}/messages", headers=H, json={"text": text})
    assert r.status_code == 200, r.text
    return r.json()["messages"][-1]


def test_ambiguous_request_is_clarified_before_any_change(make_client):
    client = make_client()
    wb = load_demo(client)
    reply = say(client, wb, "Increase labor costs by 8%, keep prices unchanged, and tell me which products lose money.")
    assert reply["payload"]["kind"] == "clarification"
    assert reply["payload"]["mode"] == "demo" and "scripted" in reply["payload"]["demo_note"]
    assert "Hourly wages only, for manufacturing" in reply["payload"]["clarification"]["options"]
    assert client.get(f"/api/workbooks/{wb['id']}/scenarios").json() == []  # nothing proposed yet

    reply = say(client, wb, "Hourly wages only, for manufacturing")
    assert reply["payload"]["kind"] == "scenario_ready"
    scenario = reply["payload"]["scenario"]
    assert scenario["changes"][0]["field_id"] == "manufacturing_hourly_rate"
    assert scenario["changes"][0]["new_value"] == pytest.approx(34.56)
    assert "Walnut Side Table" in reply["text"] and "newly unprofitable" in reply["text"]

    reply = say(client, wb, "Actually make that 5%.")
    assert reply["payload"]["kind"] == "scenario_ready"
    revised = reply["payload"]["scenario"]
    assert revised["revision_of_id"] == scenario["id"]
    assert revised["base_version"]["id"] == scenario["base_version"]["id"]
    assert revised["changes"][0]["new_value"] == pytest.approx(33.6)
    walnut = next(r for r in revised["comparison"]["tables"][0]["rows"] if r["label"] == "Walnut Side Table")
    expected = next(p for p in compute(scenario_inputs(1.05))["products"] if p["product"] == "Walnut Side Table")
    assert walnut["cells"]["profit"]["after"] == pytest.approx(expected["profit"])
    assert "No product loses money" in reply["text"]
    assert client.get(f"/api/scenarios/{scenario['id']}").json()["status"] == "superseded"


def test_explanation_uses_formulas_and_version_values(make_client):
    client = make_client()
    wb = load_demo(client)
    reply = say(client, wb, "Explain how the profit margin of Walnut Side Table is calculated.")
    assert reply["payload"]["kind"] == "answer"
    assert "Profit margin - Walnut Side Table" in reply["text"]
    assert "version 1" in reply["text"]
    base = next(p for p in compute()["products"] if p["product"] == "Walnut Side Table")
    assert f"{base['margin'] * 100:.1f}%" in reply["text"]


def test_losers_and_inputs_answers(make_client):
    client = make_client()
    wb = load_demo(client)
    assert "no product loses money" in say(client, wb, "Which products lose money?")["text"]
    inputs = say(client, wb, "What are the inputs?")["text"]
    assert "Manufacturing hourly wage: $32.00/hour" in inputs and "Payroll burden rate: 20.0%" in inputs


def test_conversation_and_state_survive_restart(make_client):
    client = make_client()
    wb = load_demo(client)
    say(client, wb, "Increase labor costs by 8%")
    client.__exit__(None, None, None)
    # Simulate a crash mid-turn: a turn left running must be reported, not silently lost.
    from app.db import session_scope
    from app.models import Turn
    with session_scope() as session:
        session.add(Turn(conversation_id=session.scalars(__import__("sqlalchemy").select(Turn)).first().conversation_id,
                         status="running", mode="demo"))
    client2 = make_client()
    listing = client2.get(f"/api/workbooks/{wb['id']}/messages").json()
    texts = [m["text"] for m in listing["messages"]]
    assert texts[0].startswith("Increase labor costs")
    assert "interrupted" in texts[-1]
    # The open clarification is persisted, so the follow-up still resolves after the restart.
    reply = say(client2, wb, "Hourly wages only, for manufacturing")
    assert reply["payload"]["kind"] == "scenario_ready"
    assert client2.get(f"/api/workbooks/{wb['id']}").json()["map"]["fields"][0]["id"] == "manufacturing_hourly_rate"
