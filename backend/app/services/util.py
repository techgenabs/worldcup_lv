import json
from datetime import datetime, timedelta


def football_goal_diff(scored: int, conceded: int) -> int:
    return scored - conceded


def cricket_nrr(scored: int, conceded: int, played: int) -> float:
    return round((scored - conceded) / max(played * 20, 1), 3)


def to_json(data) -> str:
    return json.dumps(data, default=str, ensure_ascii=False)


def round_robin_pairings(team_ids: list[int]) -> list[tuple[int, int, int]]:
    ids = team_ids[:]
    if len(ids) % 2:
        ids.append(0)
    rounds = len(ids) - 1
    half = len(ids) // 2
    pairings: list[tuple[int, int, int]] = []
    for round_no in range(rounds):
        for index in range(half):
            a = ids[index]
            b = ids[-index - 1]
            if a and b:
                pairings.append((round_no + 1, a, b))
        ids = [ids[0], ids[-1], *ids[1:-1]]
    return pairings


def fixture_date(start_date: str | None, offset: int) -> str | None:
    if not start_date:
        return None
    try:
        return (datetime.fromisoformat(start_date) + timedelta(days=offset)).isoformat()
    except ValueError:
        return start_date
