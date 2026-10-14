"""Circle challenges."""
from datetime import date, datetime, timedelta, timezone

from app.routers.challenges import _longest_run
from test_circles import _user


def _today():
    return datetime.now(timezone.utc).date()  # test users have no time zone, so the API uses UTC


def _session(client, h, weight, reps, n_sets=1):
    ex = client.get("/exercises", headers=h, params={"q": "Barbell Back Squat"}).json()[0]["id"]
    sid = client.post("/sessions", headers=h, json={"title": "Legs"}).json()["id"]
    for i in range(n_sets):
        client.post(f"/sessions/{sid}/sets", headers=h, json={"exercise_id": ex, "set_number": i + 1, "weight_kg": weight, "reps": reps})
    client.post(f"/sessions/{sid}/finish", headers=h, json={})


def _span(days_before=1, days_after=6):
    today = _today()
    return (today - timedelta(days=days_before)).isoformat(), (today + timedelta(days=days_after)).isoformat()


def test_longest_run():
    d = date(2026, 1, 1)
    assert _longest_run(set()) == 0
    assert _longest_run({d}) == 1
    assert _longest_run({d, d + timedelta(1), d + timedelta(2), d + timedelta(5), d + timedelta(6)}) == 3


def test_only_owner_creates_and_validation(client):
    owner = _user(client, "ch-own@example.com", "Olive")
    member = _user(client, "ch-mem@example.com", "Milo")
    outsider = _user(client, "ch-out@example.com", "Otto")
    cid = client.post("/circles", headers=owner, json={"name": "Challenge Club"}).json()["id"]
    client.post(f"/circles/{cid}/join", headers=member)
    start, end = _span()
    body = {"title": "October Volume", "kind": "volume", "target": 5000, "starts_on": start, "ends_on": end}

    assert client.post(f"/circles/{cid}/challenges", headers=member, json=body).status_code == 403
    assert client.post(f"/circles/{cid}/challenges", headers=outsider, json=body).status_code == 403
    assert client.post(f"/circles/{cid}/challenges", headers=owner, json={**body, "kind": "bench"}).status_code == 422
    assert client.post(f"/circles/{cid}/challenges", headers=owner, json={**body, "ends_on": "2000-01-01"}).status_code == 422
    assert client.post(f"/circles/{cid}/challenges", headers=owner, json={**body, "target": -1}).status_code == 422
    assert client.post(f"/circles/{cid}/challenges", headers=owner, json={**body, "title": "ab"}).status_code == 422
    long_end = (_today() + timedelta(days=400)).isoformat()
    assert client.post(f"/circles/{cid}/challenges", headers=owner, json={**body, "ends_on": long_end}).status_code == 422
    assert client.post(f"/circles/{cid}/challenges", headers=owner, json={**body, "kind": "streak", "target": 30}).status_code == 422

    r = client.post(f"/circles/{cid}/challenges", headers=owner, json=body)
    assert r.status_code == 201, r.text
    ch = r.json()
    assert ch["status"] == "active" and ch["days_left"] == 7 and len(ch["leaderboard"]) == 2

    assert client.get(f"/circles/{cid}/challenges", headers=outsider).status_code == 403
    assert client.get(f"/circles/{cid}/challenges/{ch['id']}", headers=outsider).status_code == 403
    assert client.get(f"/circles/{cid}/challenges/{ch['id']}", headers=member).status_code == 200
    assert client.delete(f"/circles/{cid}/challenges/{ch['id']}", headers=member).status_code == 403


def test_scores_progress_and_window(client):
    owner = _user(client, "cs-own@example.com", "Ana")
    member = _user(client, "cs-mem@example.com", "Bo")
    quiet = _user(client, "cs-q@example.com", "Cy")
    cid = client.post("/circles", headers=owner, json={"name": "Score Club"}).json()["id"]
    client.post(f"/circles/{cid}/join", headers=member)
    client.post(f"/circles/{cid}/join", headers=quiet)
    _session(client, owner, 100, 10)  # 1000
    _session(client, member, 100, 10, n_sets=3)  # 3000
    _session(client, member, 50, 10)  # +500, second workout

    start, end = _span()
    vol = client.post(f"/circles/{cid}/challenges", headers=owner, json={"title": "Volume", "kind": "volume", "target": 2000, "starts_on": start, "ends_on": end}).json()
    board = vol["leaderboard"]
    assert [(r["display_name"], r["value"]) for r in board] == [("Bo", 3500.0), ("Ana", 1000.0), ("Cy", 0.0)]
    assert [r["progress"] for r in board] == [1.0, 0.5, 0.0]  # capped at the target
    assert vol["my_value"] == 1000.0 and vol["my_rank"] == 2 and vol["leader_name"] == "Bo"

    wk = client.post(f"/circles/{cid}/challenges", headers=owner, json={"title": "Most workouts", "kind": "workouts", "starts_on": start, "ends_on": end}).json()
    assert [(r["display_name"], r["value"], r["progress"]) for r in wk["leaderboard"]] == [("Bo", 2.0, 1.0), ("Ana", 1.0, 0.5), ("Cy", 0.0, 0.0)]

    st = client.post(f"/circles/{cid}/challenges", headers=owner, json={"title": "Streak", "kind": "streak", "starts_on": start, "ends_on": end}).json()
    assert [r["value"] for r in st["leaderboard"]] == [1.0, 1.0, 0.0]  # one training day each so far

    # a challenge that already ended yesterday does not count today's workouts
    past_s, past_e = (_today() - timedelta(days=10)).isoformat(), (_today() - timedelta(days=1)).isoformat()
    past = client.post(f"/circles/{cid}/challenges", headers=owner, json={"title": "Last week", "kind": "volume", "starts_on": past_s, "ends_on": past_e}).json()
    assert past["status"] == "ended" and past["days_left"] == 0 and all(r["value"] == 0 for r in past["leaderboard"])
    assert past["leader_name"] is None

    future_s, future_e = (_today() + timedelta(days=3)).isoformat(), (_today() + timedelta(days=9)).isoformat()
    fut = client.post(f"/circles/{cid}/challenges", headers=owner, json={"title": "Next week", "kind": "workouts", "starts_on": future_s, "ends_on": future_e}).json()
    assert fut["status"] == "upcoming" and fut["days_left"] == 3

    listed = client.get(f"/circles/{cid}/challenges", headers=member).json()
    assert [c["status"] for c in listed] == ["active", "active", "active", "upcoming", "ended"]
    assert "leaderboard" not in listed[0]


def test_challenge_delete_and_circle_cascade(client):
    owner = _user(client, "cd-own@example.com")
    cid = client.post("/circles", headers=owner, json={"name": "Temp Challenge Club"}).json()["id"]
    start, end = _span()
    ch = client.post(f"/circles/{cid}/challenges", headers=owner, json={"title": "Gone soon", "kind": "workouts", "starts_on": start, "ends_on": end}).json()
    assert client.delete(f"/circles/{cid}/challenges/{ch['id']}", headers=owner).status_code == 204
    assert client.get(f"/circles/{cid}/challenges/{ch['id']}", headers=owner).status_code == 404

    ch2 = client.post(f"/circles/{cid}/challenges", headers=owner, json={"title": "Also gone", "kind": "workouts", "starts_on": start, "ends_on": end}).json()
    other = client.post("/circles", headers=owner, json={"name": "Other Club"}).json()["id"]
    # a challenge id from another circle is not reachable through this one
    assert client.get(f"/circles/{other}/challenges/{ch2['id']}", headers=owner).status_code == 404
    assert client.delete(f"/circles/{cid}", headers=owner).status_code == 204
    from app.database import SessionLocal
    from app import models

    with SessionLocal() as db:
        assert db.get(models.CircleChallenge, ch2["id"]) is None
