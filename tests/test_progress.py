"""Progress: per-exercise history, records, summaries, metric edits and the weight goal."""
from datetime import date, datetime, timedelta, timezone

from test_circles import _user


def _today():
    return datetime.now(timezone.utc).date()  # test users have no time zone, so the API uses UTC


def _ex(client, h, name):
    return client.get("/exercises", headers=h, params={"q": name}).json()[0]["id"]


def _workout(client, h, ex, sets):
    sid = client.post("/sessions", headers=h, json={"title": "Session"}).json()["id"]
    for i, (w, r) in enumerate(sets):
        client.post(f"/sessions/{sid}/sets", headers=h, json={"exercise_id": ex, "set_number": i + 1, "weight_kg": w, "reps": r})
    client.post(f"/sessions/{sid}/finish", headers=h, json={})
    return sid


def test_exercise_progress_and_logged_list(client):
    me = _user(client, "pg-me@example.com")
    other = _user(client, "pg-other@example.com")
    bench = _ex(client, me, "Barbell Bench Press")
    _workout(client, me, bench, [(60, 10), (80, 3), (70, 6)])
    _workout(client, other, bench, [(200, 5)])  # someone else's sets never leak in

    logged = client.get("/progress/exercises", headers=me).json()
    assert [(x["id"], x["sessions"]) for x in logged] == [(bench, 1)]

    p = client.get(f"/progress/exercises/{bench}", headers=me).json()
    assert p["name"] == "Barbell Bench Press" and len(p["points"]) == 1
    pt = p["points"][0]
    # Epley: 60x10 = 80.0, 80x3 = 88.0, 70x6 = 84.0 -> best set is 80 x 3
    assert (pt["best_est_1rm"], pt["best_weight_kg"], pt["best_reps"], pt["top_weight_kg"]) == (88.0, 80.0, 3, 80.0)
    assert pt["volume_kg"] == 60 * 10 + 80 * 3 + 70 * 6
    assert pt["date"] == _today().isoformat() or abs((date.fromisoformat(pt["date"]) - _today()).days) <= 1
    assert client.get("/progress/exercises/999999", headers=me).status_code == 404
    assert client.get(f"/progress/exercises/{bench}", headers=me, params={"days": 0}).status_code == 422
    assert client.get(f"/progress/exercises/{bench}", headers=_user(client, "pg-new@example.com")).json()["points"] == []


def test_personal_records(client):
    me = _user(client, "pr-me@example.com")
    squat, bench = _ex(client, me, "Barbell Back Squat"), _ex(client, me, "Barbell Bench Press")
    _workout(client, me, squat, [(100, 5), (120, 1)])
    _workout(client, me, bench, [(60, 8)])
    _workout(client, me, squat, [(100, 5)])  # not better: the record keeps its first date
    recs = {r["name"]: r for r in client.get("/progress/records", headers=me).json()}
    assert set(recs) == {"Barbell Back Squat", "Barbell Bench Press"}
    sq = recs["Barbell Back Squat"]
    assert (sq["weight_kg"], sq["reps"], sq["est_1rm"]) == (120.0, 1, 124.0) and sq["is_recent"] is True
    assert recs["Barbell Bench Press"]["est_1rm"] == 76.0
    assert client.get("/progress/records", headers=_user(client, "pr-empty@example.com")).json() == []


def test_summary_week_and_month(client):
    me = _user(client, "sm-me@example.com")
    ex = _ex(client, me, "Barbell Back Squat")
    empty = client.get("/progress/summary", headers=me).json()
    assert empty["current"] == {"workouts": 0, "volume_kg": 0.0, "active_minutes": 0}
    assert empty["change"] == {"workouts_pct": None, "volume_pct": None, "minutes_pct": None}

    _workout(client, me, ex, [(100, 10)])
    _workout(client, me, ex, [(50, 10)])
    open_sid = client.post("/sessions", headers=me, json={}).json()["id"]  # unfinished: ignored
    client.post(f"/sessions/{open_sid}/sets", headers=me, json={"exercise_id": ex, "weight_kg": 500, "reps": 10})
    wk = client.get("/progress/summary", headers=me, params={"period": "week"}).json()
    assert wk["days"] == 7 and wk["current"]["workouts"] == 2 and wk["current"]["volume_kg"] == 1500.0
    assert wk["previous"]["workouts"] == 0 and wk["change"]["workouts_pct"] is None
    mo = client.get("/progress/summary", headers=me, params={"period": "month"}).json()
    assert mo["days"] == 30 and mo["current"]["workouts"] == 2
    assert date.fromisoformat(mo["end"]) - date.fromisoformat(mo["start"]) == timedelta(days=29)
    assert client.get("/progress/summary", headers=me, params={"period": "year"}).status_code == 422


def test_edit_metric(client):
    me = _user(client, "em-me@example.com")
    other = _user(client, "em-other@example.com")
    old = client.post("/metrics", headers=me, json={"weight_kg": 82, "waist_cm": 90, "logged_on": (_today() - timedelta(days=5)).isoformat()}).json()
    new = client.post("/metrics", headers=me, json={"weight_kg": 80}).json()

    r = client.patch(f"/metrics/{old['id']}", headers=me, json={"waist_cm": 88.5, "body_fat_pct": 18})
    assert r.status_code == 200 and r.json()["waist_cm"] == 88.5 and r.json()["weight_kg"] == 82
    # editing an older entry doesn't move the headline weight; editing the newest does
    assert client.get("/me", headers=me).json()["profile"]["weight_kg"] == 80
    client.patch(f"/metrics/{new['id']}", headers=me, json={"weight_kg": 79.5})
    assert client.get("/me", headers=me).json()["profile"]["weight_kg"] == 79.5

    assert client.patch(f"/metrics/{old['id']}", headers=other, json={"waist_cm": 70}).status_code == 404
    assert client.patch(f"/metrics/{old['id']}", headers=me, json={"waist_cm": 5}).status_code == 422
    future = (_today() + timedelta(days=3)).isoformat()
    assert client.patch(f"/metrics/{old['id']}", headers=me, json={"logged_on": future}).status_code == 422
    only_weight = client.post("/metrics", headers=me, json={"weight_kg": 81, "logged_on": (_today() - timedelta(days=9)).isoformat()}).json()
    assert client.patch(f"/metrics/{only_weight['id']}", headers=me, json={"weight_kg": None}).status_code == 422
    assert client.get("/metrics", headers=me).json()[-1]["weight_kg"] == 81  # unchanged after the rejected edit


def test_target_weight_goal(client, auth):
    h, _ = auth
    assert client.get("/me", headers=h).json()["profile"]["target_weight_kg"] is None
    assert client.patch("/me", headers=h, json={"target_weight_kg": 75}).json()["profile"]["target_weight_kg"] == 75
    assert client.patch("/me", headers=h, json={"target_weight_kg": 10}).status_code == 422
    # other fields leave the goal alone; an explicit null clears it
    assert client.patch("/me", headers=h, json={"days_per_week": 5}).json()["profile"]["target_weight_kg"] == 75
    assert client.patch("/me", headers=h, json={"target_weight_kg": None}).json()["profile"]["target_weight_kg"] is None
