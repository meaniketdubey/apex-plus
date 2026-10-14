import json

import pytest

from app.ai.llm import LLMError, get_llm
from app.main import app


class FakeLLM:
    """Scripted stand-in for the model. Exceptions in `replies` are raised."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def complete(self, system, messages, max_tokens=800):
        self.calls.append({"system": system, "messages": messages})
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture()
def fake_llm():
    holder = {}

    def install(*replies):
        holder["llm"] = FakeLLM(*replies)
        app.dependency_overrides[get_llm] = lambda: holder["llm"]
        return holder["llm"]

    yield install
    app.dependency_overrides.pop(get_llm, None)


def _ex(client, h, name):
    return next(e for e in client.get("/exercises", headers=h, params={"q": name}).json() if e["name"] == name)


def _log(client, h, name, sets):
    ex = _ex(client, h, name)
    sid = client.post("/sessions", headers=h, json={}).json()["id"]
    for i, (w, r) in enumerate(sets, 1):
        client.post(f"/sessions/{sid}/sets", headers=h, json={"exercise_id": ex["id"], "set_number": i, "weight_kg": w, "reps": r})
    client.post(f"/sessions/{sid}/finish", headers=h, json={})
    return ex


# ----------------------------------------------------------- status/auth
def test_ai_requires_login(client):
    for method, path in [("get", "/ai/status"), ("post", "/ai/parse-workout"), ("get", "/ai/insight"), ("post", "/ai/chat")]:
        assert getattr(client, method)(path).status_code == 401


def test_status_without_key(client, auth):
    assert client.get("/ai/status", headers=auth[0]).json() == {"llm_enabled": False}


# ----------------------------------------------------------- name matching
def test_match_exercise_aliases_and_fuzz(client, auth):
    from types import SimpleNamespace

    from app.ai.parse import match_exercise

    lib = [SimpleNamespace(name=n) for n in ["Barbell Back Squat", "Barbell Bench Press", "Incline Barbell Bench Press", "Pull-Up", "Push-Up", "Overhead Press", "Romanian Deadlift", "Deadlift"]]
    m = lambda t: (match_exercise(t, lib) or SimpleNamespace(name=None)).name  # noqa: E731
    assert m("squats") == "Barbell Back Squat"
    assert m("bench") == "Barbell Bench Press"
    assert m("incline bench") == "Incline Barbell Bench Press"
    assert m("pull ups") == "Pull-Up"
    assert m("OHP") == "Overhead Press"
    assert m("deadlift") == "Deadlift"
    assert m("romanian dead lift") == "Romanian Deadlift"  # fuzzy
    assert m("underwater basket weaving") is None


# ----------------------------------------------------------- parse-workout
def test_parse_workout_rules(client, auth):
    h, _ = auth
    r = client.post("/ai/parse-workout", headers=h, json={"text": "3x10 squats at 80kg, bench 80x8 85x8 and zorbo 3x5 40kg"}).json()
    assert r["source"] == "rules"
    names = [i["exercise_name"] for i in r["items"]]
    assert names == ["Barbell Back Squat", "Barbell Bench Press"]
    assert r["items"][0]["sets"] == [{"weight_kg": 80.0, "reps": 10}] * 3
    assert r["items"][1]["sets"] == [{"weight_kg": 80.0, "reps": 8}, {"weight_kg": 85.0, "reps": 8}]
    assert r["unmatched"] == ["zorbo"]
    # nothing is written
    assert client.get("/sessions", headers=h).json() == []


def test_parse_workout_nothing_loggable(client, auth):
    r = client.post("/ai/parse-workout", headers=auth[0], json={"text": "great day, felt strong"}).json()
    assert r == {"items": [], "unmatched": [], "source": "rules"}
    assert client.post("/ai/parse-workout", headers=auth[0], json={"text": ""}).status_code == 422
    assert client.post("/ai/parse-workout", headers=auth[0], json={"text": "x" * 1001}).status_code == 422


def test_parse_workout_llm_and_fallbacks(client, auth, fake_llm):
    h, _ = auth
    good = 'Sure!\n```json\n[{"exercise": "Deadlift", "sets": [{"weight_kg": 140, "reps": 5}, {"weight_kg": 140, "reps": 5}]}]\n```'
    llm = fake_llm(good)
    r = client.post("/ai/parse-workout", headers=h, json={"text": "deadlifted 140 for 2 sets of 5"}).json()
    assert r["source"] == "llm" and r["items"][0]["exercise_name"] == "Deadlift" and len(r["items"][0]["sets"]) == 2
    assert "Deadlift" in llm.calls[0]["messages"][0]["content"]  # library is sent to the model

    # unusable output -> rules take over
    fake_llm("I could not do that")
    r = client.post("/ai/parse-workout", headers=h, json={"text": "3x10 squats at 80kg"}).json()
    assert r["source"] == "rules" and r["items"][0]["exercise_name"] == "Barbell Back Squat"
    # model outage -> rules take over
    fake_llm(LLMError("overloaded"))
    assert client.post("/ai/parse-workout", headers=h, json={"text": "3x10 squats at 80kg"}).json()["source"] == "rules"
    # model returns junk values -> they are dropped, not trusted
    fake_llm(json.dumps([{"exercise": "Deadlift", "sets": [{"weight_kg": 99999, "reps": 5}, {"weight_kg": 100, "reps": 0}]}]))
    assert client.post("/ai/parse-workout", headers=h, json={"text": "nothing parseable here"}).json()["items"] == []


# ----------------------------------------------------------- next weight
def test_next_weight_rules(client, auth):
    h, _ = auth
    squat = _ex(client, h, "Barbell Back Squat")
    bench = _ex(client, h, "Barbell Bench Press")
    pull = _ex(client, h, "Pull-Up")

    none = client.get("/ai/next-weight", headers=h, params={"exercise_id": bench["id"]}).json()
    assert none["suggested_weight_kg"] is None and "No history" in none["reason"]

    _log(client, h, "Barbell Back Squat", [(100, 8), (100, 8), (100, 9)])
    assert client.get("/ai/next-weight", headers=h, params={"exercise_id": squat["id"]}).json()["suggested_weight_kg"] == 105.0  # legs compound: +5

    _log(client, h, "Barbell Bench Press", [(60, 10), (60, 9), (60, 8)])
    assert client.get("/ai/next-weight", headers=h, params={"exercise_id": bench["id"]}).json()["suggested_weight_kg"] == 62.5  # +2.5

    _log(client, h, "Barbell Bench Press", [(62.5, 8), (62.5, 8), (62.5, 6)])
    stay = client.get("/ai/next-weight", headers=h, params={"exercise_id": bench["id"]}).json()
    assert stay["suggested_weight_kg"] == 62.5 and "lowest was 6" in stay["reason"]

    _log(client, h, "Barbell Bench Press", [(62.5, 8), (62.5, 8), (62.5, 3)])
    back = client.get("/ai/next-weight", headers=h, params={"exercise_id": bench["id"]}).json()
    assert back["suggested_weight_kg"] == 60.0 and back["last_weight_kg"] == 62.5  # 5% down, rounded to 2.5

    _log(client, h, "Pull-Up", [(0, 8), (0, 7), (0, 6)])
    bw = client.get("/ai/next-weight", headers=h, params={"exercise_id": pull["id"]}).json()
    assert bw["suggested_weight_kg"] == 0 and "9-10" in bw["reason"]

    assert client.get("/ai/next-weight", headers=h, params={"exercise_id": 99999}).status_code == 404


# ----------------------------------------------------------- insight
def test_insight(client, auth):
    h, _ = auth
    empty = client.get("/ai/insight", headers=h).json()
    assert "No workouts" in empty["headline"] and empty["stats"]["streak_days"] == 0

    _log(client, h, "Barbell Back Squat", [(100, 10), (100, 10)])
    r = client.get("/ai/insight", headers=h).json()
    assert r["headline"] == "1 of 4 planned workouts this week"
    text = " ".join(r["bullets"])
    assert "3 more sessions" in text and "2,000 kg" in text and "legs" in text and "chest" in text
    assert r["stats"]["this_week"]["workouts"] == 1 and r["stats"]["streak_days"] == 1


# ----------------------------------------------------------- generator
def test_generate_routine_rules(client, auth):
    h, _ = auth
    r = client.post("/ai/generate-routine", headers=h, json={"goal": "Strength", "minutes": 45, "focus": "full body"}).json()
    assert r["source"] == "rules" and 3 <= len(r["items"]) <= 8
    assert len({i["exercise_id"] for i in r["items"]}) == len(r["items"])  # no duplicates
    assert r["items"][0]["target_sets"] == 4 and r["items"][0]["target_reps"] == 5  # strength scheme on a compound
    assert len({i["muscle_group"] for i in r["items"]}) >= 3  # spread across muscle groups

    lower = client.post("/ai/generate-routine", headers=h, json={"focus": "lower", "minutes": 60}).json()
    assert {i["muscle_group"] for i in lower["items"]} <= {"legs", "core"}

    bw = client.post("/ai/generate-routine", headers=h, json={"equipment": ["bodyweight"]}).json()
    all_ex = {e["id"]: e for e in client.get("/exercises", headers=h).json()}
    assert all(all_ex[i["exercise_id"]]["equipment"] == "bodyweight" for i in bw["items"])

    assert client.post("/ai/generate-routine", headers=h, json={"focus": "nonsense"}).status_code == 422
    assert client.post("/ai/generate-routine", headers=h, json={"minutes": 5}).status_code == 422

    # the draft can be saved through the normal routines endpoint
    saved = client.post("/routines", headers=h, json={"title": r["title"], "items": [{k: i[k] for k in ("exercise_id", "target_sets", "target_reps", "rest_seconds")} for i in r["items"]]})
    assert saved.status_code == 201


def test_generate_routine_llm_is_validated(client, auth, fake_llm):
    h, _ = auth
    ids = [e["id"] for e in client.get("/exercises", headers=h).json()][:5]
    good = json.dumps({"title": "Coach Pick", "description": "Tailored.", "items": [{"exercise_id": i, "target_sets": 99, "target_reps": 8, "rest_seconds": 5} for i in ids]})
    fake_llm(good)
    r = client.post("/ai/generate-routine", headers=h, json={"minutes": 60}).json()
    assert r["source"] == "llm" and r["title"] == "Coach Pick"
    assert all(i["target_sets"] == 10 and i["rest_seconds"] == 15 for i in r["items"])  # clamped, not trusted

    # hallucinated ids -> fall back to the rule-based routine
    fake_llm(json.dumps({"title": "x", "items": [{"exercise_id": 9999 + n} for n in range(5)]}))
    assert client.post("/ai/generate-routine", headers=h, json={}).json()["source"] == "rules"
    fake_llm("not json at all")
    assert client.post("/ai/generate-routine", headers=h, json={}).json()["source"] == "rules"


# ----------------------------------------------------------- chat
def test_chat_unavailable_without_key(client, auth):
    r = client.post("/ai/chat", headers=auth[0], json={"message": "hi"})
    assert r.status_code == 503 and "ANTHROPIC_API_KEY" in r.json()["detail"]


def test_chat_flow(client, auth, fake_llm):
    h, _ = auth
    client.patch("/me", headers=h, json={"goal": "Strength"})
    _log(client, h, "Barbell Bench Press", [(80, 8)])
    llm = fake_llm("Nice work. Add 2.5 kg next time.", "Rest more.")

    assert client.post("/ai/chat", headers=h, json={"message": "How is my bench?"}).json() == {"reply": "Nice work. Add 2.5 kg next time."}
    first = llm.calls[0]
    assert "Goal: Strength" in first["system"] and "Barbell Bench Press: 80x8" in first["system"]  # grounded in the user's data
    assert "not a doctor" in first["system"]
    assert first["messages"] == [{"role": "user", "content": "How is my bench?"}]

    client.post("/ai/chat", headers=h, json={"message": "And recovery?"})
    second = llm.calls[1]["messages"]
    assert [m["role"] for m in second] == ["user", "assistant", "user"]  # history is replayed

    hist = client.get("/ai/chat/history", headers=h).json()
    assert [m["role"] for m in hist] == ["user", "assistant", "user", "assistant"]
    assert client.delete("/ai/chat/history", headers=h).status_code == 204
    assert client.get("/ai/chat/history", headers=h).json() == []


def test_chat_errors_and_limits(client, auth, fake_llm, monkeypatch):
    h, _ = auth
    fake_llm(LLMError("down"))
    r = client.post("/ai/chat", headers=h, json={"message": "hi"})
    assert r.status_code == 502
    assert client.get("/ai/chat/history", headers=h).json() == []  # failed turns are not stored

    from app.config import settings

    monkeypatch.setattr(settings, "ai_chat_daily_limit", 1)
    fake_llm("ok", "should not be reached")
    assert client.post("/ai/chat", headers=h, json={"message": "one"}).status_code == 200
    assert client.post("/ai/chat", headers=h, json={"message": "two"}).status_code == 429
    assert client.post("/ai/chat", headers=h, json={"message": ""}).status_code == 422
