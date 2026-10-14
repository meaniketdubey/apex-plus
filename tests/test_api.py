def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


# ------------------------------------------------------------------ auth
def test_register_login_refresh(client):
    r = client.post("/auth/register", json={"email": "Alex@Example.com", "password": "supersecret1"})
    assert r.status_code == 201
    tokens = r.json()

    # email is case-insensitive
    assert client.post("/auth/register", json={"email": "alex@example.com", "password": "supersecret1"}).status_code == 409

    ok = client.post("/auth/login", json={"email": "ALEX@example.com", "password": "supersecret1"})
    assert ok.status_code == 200
    bad = client.post("/auth/login", json={"email": "alex@example.com", "password": "wrong-password"})
    assert bad.status_code == 401
    unknown = client.post("/auth/login", json={"email": "nobody@example.com", "password": "supersecret1"})
    assert unknown.status_code == 401
    assert bad.json() == unknown.json()  # no account enumeration

    r = client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 200
    # an access token must not work as a refresh token, and vice versa
    assert client.post("/auth/refresh", json={"refresh_token": tokens["access_token"]}).status_code == 401
    me = client.get("/me", headers={"Authorization": f"Bearer {tokens['refresh_token']}"})
    assert me.status_code == 401


def test_weak_password_rejected(client):
    r = client.post("/auth/register", json={"email": "weak@example.com", "password": "short"})
    assert r.status_code == 422


def test_protected_routes_need_token(client):
    for path in ("/me", "/metrics", "/routines", "/exercises", "/sessions", "/dashboard"):
        assert client.get(path).status_code == 401, path
    assert client.get("/me", headers={"Authorization": "Bearer junk"}).status_code == 401


# --------------------------------------------------------------- profile
def test_profile_update(client, auth):
    headers, email = auth
    me = client.get("/me", headers=headers).json()
    assert me["email"] == email and me["profile"]["onboarded"] is False

    r = client.patch(
        "/me",
        headers=headers,
        json={"height_cm": 182, "weight_kg": 82.5, "goal": "Strength", "days_per_week": 5, "onboarded": True},
    )
    assert r.status_code == 200
    p = r.json()["profile"]
    assert p["height_cm"] == 182 and p["goal"] == "Strength" and p["onboarded"] is True

    assert client.patch("/me", headers=headers, json={"height_cm": 5}).status_code == 422


# --------------------------------------------------------------- metrics
def test_metrics_flow(client, auth):
    headers, _ = auth
    assert client.post("/metrics", headers=headers, json={}).status_code == 422
    assert client.post("/metrics", headers=headers, json={"weight_kg": 5}).status_code == 422

    r = client.post("/metrics", headers=headers, json={"weight_kg": 82.5, "chest_cm": 108, "logged_on": "2026-10-04"})
    assert r.status_code == 201
    mid = r.json()["id"]
    client.post("/metrics", headers=headers, json={"weight_kg": 82.0, "logged_on": "2026-10-06"})

    rows = client.get("/metrics", headers=headers).json()
    assert [m["weight_kg"] for m in rows] == [82.0, 82.5]  # newest first
    assert client.get("/me", headers=headers).json()["profile"]["weight_kg"] == 82.0

    assert client.delete(f"/metrics/{mid}", headers=headers).status_code == 204
    assert len(client.get("/metrics", headers=headers).json()) == 1


def test_metrics_are_private(client, auth):
    headers, _ = auth
    mid = client.post("/metrics", headers=headers, json={"weight_kg": 80}).json()["id"]
    other = client.post("/auth/register", json={"email": "intruder@example.com", "password": "supersecret1"}).json()
    oh = {"Authorization": f"Bearer {other['access_token']}"}
    assert client.get("/metrics", headers=oh).json() == []
    assert client.delete(f"/metrics/{mid}", headers=oh).status_code == 404


def test_water(client, auth):
    headers, _ = auth
    assert client.get("/water", headers=headers).json()["litres"] == 0
    client.post("/water", headers=headers, json={"add_litres": 0.5})
    assert client.post("/water", headers=headers, json={"add_litres": 0.25}).json()["litres"] == 0.75
    assert client.post("/water", headers=headers, json={"add_litres": 0}).status_code == 422


# -------------------------------------------------------------- training
def test_seed_data_available(client, auth):
    headers, _ = auth
    routines = client.get("/routines", headers=headers).json()
    assert len(routines) >= 3
    assert all(len(r["items"]) >= 5 for r in routines)
    assert routines[0]["items"][0]["exercise"]["name"]
    assert len(client.get("/exercises", headers=headers).json()) >= 20
    legs = client.get("/exercises", headers=headers, params={"muscle_group": "legs"}).json()
    assert legs and all(e["muscle_group"] == "legs" for e in legs)
    squat = client.get("/exercises", headers=headers, params={"q": "squat"}).json()
    assert any("Squat" in e["name"] for e in squat)


def _exercise_id(client, headers, name):
    return next(e["id"] for e in client.get("/exercises", headers=headers, params={"q": name}).json() if e["name"] == name)


def test_custom_routine(client, auth):
    headers, _ = auth
    bench = _exercise_id(client, headers, "Barbell Bench Press")
    r = client.post(
        "/routines",
        headers=headers,
        json={"title": "My Push Day", "items": [{"exercise_id": bench, "target_sets": 5, "target_reps": 5}]},
    )
    assert r.status_code == 201
    rid = r.json()["id"]
    assert r.json()["owner_id"] is not None

    assert client.post("/routines", headers=headers, json={"title": "x", "items": [{"exercise_id": 99999}]}).status_code == 422

    other = client.post("/auth/register", json={"email": "other-r@example.com", "password": "supersecret1"}).json()
    oh = {"Authorization": f"Bearer {other['access_token']}"}
    assert client.get(f"/routines/{rid}", headers=oh).status_code == 404
    assert rid not in [x["id"] for x in client.get("/routines", headers=oh).json()]
    assert client.delete(f"/routines/{rid}", headers=oh).status_code == 404
    assert client.delete(f"/routines/{rid}", headers=headers).status_code == 204


def test_session_flow_and_pr_detection(client, auth):
    headers, _ = auth
    bench = _exercise_id(client, headers, "Barbell Bench Press")
    routine_id = client.get("/routines", headers=headers).json()[0]["id"]

    s1 = client.post("/sessions", headers=headers, json={"routine_id": routine_id})
    assert s1.status_code == 201
    sid = s1.json()["id"]
    assert s1.json()["title"] == "Upper Body Power & Pump"

    # First ever set is a baseline, not a PR.
    a = client.post(f"/sessions/{sid}/sets", headers=headers, json={"exercise_id": bench, "set_number": 1, "weight_kg": 80, "reps": 8})
    assert a.status_code == 201 and a.json()["is_pr"] is False
    # Heavier same-reps set beats the baseline.
    b = client.post(f"/sessions/{sid}/sets", headers=headers, json={"exercise_id": bench, "set_number": 2, "weight_kg": 85, "reps": 8})
    assert b.json()["is_pr"] is True
    # Lighter set is not a PR.
    c = client.post(f"/sessions/{sid}/sets", headers=headers, json={"exercise_id": bench, "set_number": 3, "weight_kg": 70, "reps": 8})
    assert c.json()["is_pr"] is False

    assert client.post(f"/sessions/{sid}/sets", headers=headers, json={"exercise_id": 99999, "weight_kg": 10, "reps": 5}).status_code == 422
    assert client.post(f"/sessions/{sid}/sets", headers=headers, json={"exercise_id": bench, "weight_kg": -5, "reps": 5}).status_code == 422

    # Deleting a set works.
    assert client.delete(f"/sessions/{sid}/sets/{c.json()['id']}", headers=headers).status_code == 204

    fin = client.post(f"/sessions/{sid}/finish", headers=headers, json={"notes": "felt strong"})
    assert fin.status_code == 200 and fin.json()["finished_at"] is not None and len(fin.json()["sets"]) == 2
    # Finishing twice is harmless; logging after finish is rejected.
    assert client.post(f"/sessions/{sid}/finish", headers=headers, json={}).status_code == 200
    late = client.post(f"/sessions/{sid}/sets", headers=headers, json={"exercise_id": bench, "weight_kg": 90, "reps": 5})
    assert late.status_code == 409

    # Ghost values for the next session come from history.
    s2 = client.post("/sessions", headers=headers, json={"title": "Second"}).json()["id"]
    h = client.get(f"/exercises/{bench}/history", headers=headers, params={"exclude_session_id": s2}).json()
    assert h["session_id"] == sid and [x["weight_kg"] for x in h["sets"]] == [80, 85]
    assert h["best_est_1rm"] == round(85 * (1 + 8 / 30), 1)
    empty = client.get(f"/exercises/{_exercise_id(client, headers, 'Plank')}/history", headers=headers).json()
    assert empty["sets"] == [] and empty["best_est_1rm"] is None


def test_empty_session_cannot_finish_but_can_be_discarded(client, auth):
    headers, _ = auth
    sid = client.post("/sessions", headers=headers, json={}).json()["id"]
    assert client.post(f"/sessions/{sid}/finish", headers=headers, json={}).status_code == 409
    assert client.delete(f"/sessions/{sid}", headers=headers).status_code == 204
    assert client.get(f"/sessions/{sid}", headers=headers).status_code == 404
    # an empty discarded session never counts towards stats
    assert client.get("/dashboard", headers=headers).json()["total_workouts"] == 0


def test_sessions_are_private(client, auth):
    headers, _ = auth
    sid = client.post("/sessions", headers=headers, json={}).json()["id"]
    other = client.post("/auth/register", json={"email": "other-s@example.com", "password": "supersecret1"}).json()
    oh = {"Authorization": f"Bearer {other['access_token']}"}
    assert client.get(f"/sessions/{sid}", headers=oh).status_code == 404
    assert client.post(f"/sessions/{sid}/finish", headers=oh, json={}).status_code == 404
    assert client.delete(f"/sessions/{sid}", headers=oh).status_code == 404
    assert client.get("/sessions", headers=oh).json() == []


# ------------------------------------------------------------- dashboard
def test_dashboard_empty_user(client, auth):
    headers, _ = auth
    d = client.get("/dashboard", headers=headers).json()
    assert d["streak_days"] == 0 and d["total_workouts"] == 0 and d["latest_metric"] is None
    assert [m["muscle_group"] for m in d["muscle_load"]] == ["legs", "chest", "back", "shoulders", "arms", "core"]
    assert all(m["percent"] == 0 and m["label"] == "Rested" for m in d["muscle_load"])


def test_dashboard_with_activity(client, auth):
    headers, _ = auth
    squat = _exercise_id(client, headers, "Barbell Back Squat")
    bench = _exercise_id(client, headers, "Barbell Bench Press")

    sid = client.post("/sessions", headers=headers, json={"title": "Legs + chest"}).json()["id"]
    client.post(f"/sessions/{sid}/sets", headers=headers, json={"exercise_id": squat, "weight_kg": 100, "reps": 10})
    client.post(f"/sessions/{sid}/sets", headers=headers, json={"exercise_id": bench, "weight_kg": 50, "reps": 10})
    client.post(f"/sessions/{sid}/finish", headers=headers, json={})
    client.post("/metrics", headers=headers, json={"weight_kg": 82.5, "body_fat_pct": 14.2})
    client.post("/water", headers=headers, json={"add_litres": 1.5})

    d = client.get("/dashboard", headers=headers).json()
    assert d["streak_days"] == 1 and d["total_workouts"] == 1 and d["workouts_this_week"] == 1
    load = {m["muscle_group"]: m for m in d["muscle_load"]}
    assert load["legs"]["percent"] == 100 and load["legs"]["label"] == "High"
    assert load["chest"]["percent"] == 50 and load["chest"]["label"] == "Optimal"
    assert load["back"]["percent"] == 0
    assert d["latest_metric"]["weight_kg"] == 82.5
    assert d["weight_history"][-1]["body_fat_pct"] == 14.2
    assert d["water_litres_today"] == 1.5


def test_streak_logic():
    from datetime import date, timedelta

    from app.routers.dashboard import compute_streak

    today = date(2026, 10, 7)
    d = lambda n: today - timedelta(days=n)  # noqa: E731
    assert compute_streak(set(), today) == 0
    assert compute_streak({d(0), d(1), d(2)}, today) == 3
    assert compute_streak({d(1), d(2)}, today) == 2  # streak alive, today not logged yet
    assert compute_streak({d(2), d(3)}, today) == 0  # broken
    assert compute_streak({d(0), d(2)}, today) == 1  # gap


# ------------------------------------------------- time zones / idempotency
def test_timezone_registration_and_update(client):
    r = client.post("/auth/register", json={"email": "tz@example.com", "password": "supersecret1", "timezone": "Asia/Kolkata"})
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert client.get("/me", headers=h).json()["profile"]["timezone"] == "Asia/Kolkata"
    assert client.patch("/me", headers=h, json={"timezone": "Mars/Olympus"}).status_code == 422
    assert client.patch("/me", headers=h, json={"timezone": "Europe/London"}).json()["profile"]["timezone"] == "Europe/London"
    # unknown zone at signup falls back to UTC instead of failing the signup
    r2 = client.post("/auth/register", json={"email": "tz2@example.com", "password": "supersecret1", "timezone": "Nope/Nope"})
    h2 = {"Authorization": f"Bearer {r2.json()['access_token']}"}
    assert client.get("/me", headers=h2).json()["profile"]["timezone"] == "UTC"


def test_local_date_uses_user_zone():
    from datetime import datetime, timezone
    from types import SimpleNamespace

    from app.timeutil import local_date

    kolkata = SimpleNamespace(profile=SimpleNamespace(timezone="Asia/Kolkata"))
    late_utc = datetime(2026, 10, 7, 20, 0, tzinfo=timezone.utc)  # 01:30 next day in India
    assert local_date(late_utc, kolkata).isoformat() == "2026-10-08"
    assert local_date(late_utc.replace(tzinfo=None), kolkata).isoformat() == "2026-10-08"  # naive = UTC
    assert local_date(late_utc, SimpleNamespace(profile=SimpleNamespace(timezone="UTC"))).isoformat() == "2026-10-07"


def test_set_logging_is_idempotent(client, auth):
    headers, _ = auth
    bench = _exercise_id(client, headers, "Barbell Bench Press")
    sid = client.post("/sessions", headers=headers, json={}).json()["id"]
    body = {"exercise_id": bench, "set_number": 1, "weight_kg": 60, "reps": 10, "client_id": "abc-123"}
    a = client.post(f"/sessions/{sid}/sets", headers=headers, json=body)
    b = client.post(f"/sessions/{sid}/sets", headers=headers, json=body)  # retry after a dropped response
    assert a.status_code == 201 and b.status_code == 201
    assert a.json()["id"] == b.json()["id"]
    assert len(client.get(f"/sessions/{sid}", headers=headers).json()["sets"]) == 1
    # a different client_id is a different set
    client.post(f"/sessions/{sid}/sets", headers=headers, json={**body, "client_id": "abc-124", "set_number": 2})
    assert len(client.get(f"/sessions/{sid}", headers=headers).json()["sets"]) == 2


def test_legacy_timezone_alias_is_accepted(client, auth):
    """Browsers still report old names like Asia/Calcutta; the server must not reject them."""
    headers, _ = auth
    r = client.patch("/me", json={"timezone": "Asia/Calcutta"}, headers=headers)
    assert r.status_code == 200
    assert r.json()["profile"]["timezone"] == "Asia/Calcutta"
