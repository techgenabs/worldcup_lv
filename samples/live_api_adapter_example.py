"""
Example extension point for real-time score providers such as API-Football or CricAPI.

Keep provider credentials in environment variables. Normalize external payloads into
the local match shape before updating scores through /api/matches/{id}/score.
"""

from dataclasses import dataclass


@dataclass
class LiveScore:
    provider_match_id: str
    home_score: int
    away_score: int
    status: str


def normalize_api_football(payload: dict) -> LiveScore:
    fixture = payload["fixture"]
    goals = payload["goals"]
    return LiveScore(
        provider_match_id=str(fixture["id"]),
        home_score=int(goals.get("home") or 0),
        away_score=int(goals.get("away") or 0),
        status=fixture["status"]["short"],
    )
