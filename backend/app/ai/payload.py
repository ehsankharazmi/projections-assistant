"""The structured UI payload returned at the end of every assistant turn.

Claude produces it through structured outputs (`output_config.format`); the
server validates it again and then overwrites or adds every authoritative value
(scenario details, computed figures, version ids, Apply permission).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

PayloadKind = Literal["clarification", "answer", "scenario_pending", "scenario_ready", "error"]


class Clarification(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(max_length=500)
    options: list[str] = Field(max_length=5)


class ProposedRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(max_length=600)


class ModelPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: PayloadKind
    message: str = Field(max_length=6000)
    clarification: Clarification | None
    scenario_id: str | None
    referenced_field_ids: list[str] = Field(max_length=30)
    proposed_rule: ProposedRule | None


PAYLOAD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["kind", "message", "clarification", "scenario_id", "referenced_field_ids", "proposed_rule"],
    "properties": {
        "kind": {"type": "string", "enum": ["clarification", "answer", "scenario_pending", "scenario_ready", "error"]},
        "message": {"type": "string", "description": "Plain-language reply to the user. No cell addresses unless "
                                                     "asked. Quote only numbers returned by tools."},
        "clarification": {"anyOf": [{"type": "null"}, {
            "type": "object", "additionalProperties": False, "required": ["question", "options"],
            "properties": {"question": {"type": "string"},
                           "options": {"type": "array", "items": {"type": "string"},
                                       "description": "Two to four short answer choices."}}}]},
        "scenario_id": {"anyOf": [{"type": "string"}, {"type": "null"}],
                        "description": "The scenario this reply presents, if any."},
        "referenced_field_ids": {"type": "array", "items": {"type": "string"}},
        "proposed_rule": {"anyOf": [{"type": "null"}, {
            "type": "object", "additionalProperties": False, "required": ["text"],
            "properties": {"text": {"type": "string", "description": "A business-meaning correction the user "
                                                                     "gave, phrased as a rule to save."}}}]},
    },
}
