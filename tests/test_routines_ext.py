"""Editing and duplicating routines, and `last_done_at`."""
from test_circles import _user


def _ex(client, h, name):
    return client.get("/exercises", headers=h, params={"q": name}).json()[0]["id"]


def test_edit_own_routine_only(client):
    me = _user(client, "re-me@example.com")
    other = _user(client, "re-other@example.com")
    bench, squat = _ex(client, me, "Barbell Bench Press"), _ex(client, me, "Barbell Back Squat")
    r = client.post("/routines", headers=me, json={"title": "Push", "items": [{"exercise_id": bench}]}).json()

    body = {
        "title": "Push v2",
        "description": "heavier",
        "category": "Strength",
        "level": "Advanced",
        "est_minutes": 50,
        "items": [{"exercise_id": squat, "target_sets": 5, "target_reps": 5}, {"exercise_id": bench, "target_sets": 4, "target_reps": 8}],
    }
    up = client.put(f"/routines/{r['id']}", headers=me, json=body)
    assert up.status_code == 200, up.text
    j = up.json()
    assert (j["title"], j["category"], j["level"], j["est_minutes"]) == ("Push v2", "Strength", "Advanced", 50)
    assert [(i["exercise"]["id"], i["position"], i["target_sets"]) for i in j["items"]] == [(squat, 0, 5), (bench, 1, 4)]
    assert client.get(f"/routines/{r['id']}", headers=me).json()["items"] == j["items"]

    assert client.put(f"/routines/{r['id']}", headers=other, json=body).status_code == 404
    built_in = next(x for x in client.get("/routines", headers=me).json() if x["owner_id"] is None)
    assert client.put(f"/routines/{built_in['id']}", headers=me, json=body).status_code == 404
    bad = {**body, "items": [{"exercise_id": 999999}]}
    assert client.put(f"/routines/{r['id']}", headers=me, json=bad).status_code == 422
    assert client.put(f"/routines/{r['id']}", headers=me, json={**body, "items": []}).status_code == 422
    assert client.put(f"/routines/{r['id']}", headers=me, json={**body, "level": "x" * 21}).status_code == 422


def test_duplicate_builtin_and_own(client):
    me = _user(client, "rd-me@example.com")
    other = _user(client, "rd-other@example.com")
    built_in = next(x for x in client.get("/routines", headers=me).json() if x["owner_id"] is None)
    d = client.post(f"/routines/{built_in['id']}/duplicate", headers=me)
    assert d.status_code == 201
    copy = d.json()
    assert copy["id"] != built_in["id"] and copy["owner_id"] is not None
    assert copy["title"] == f"{built_in['title']} (copy)"
    assert [i["exercise"]["id"] for i in copy["items"]] == [i["exercise"]["id"] for i in built_in["items"]]
    # the copy is editable and private to me
    assert client.get(f"/routines/{copy['id']}", headers=other).status_code == 404
    assert client.post(f"/routines/{copy['id']}/duplicate", headers=other).status_code == 404
    assert client.post("/routines/999999/duplicate", headers=me).status_code == 404


def test_last_done_at_is_per_user(client):
    me = _user(client, "ld-me@example.com")
    other = _user(client, "ld-other@example.com")
    rid = client.get("/routines", headers=me).json()[0]["id"]
    assert client.get(f"/routines/{rid}", headers=me).json()["last_done_at"] is None

    ex = client.get(f"/routines/{rid}", headers=me).json()["items"][0]["exercise"]["id"]
    sid = client.post("/sessions", headers=me, json={"routine_id": rid}).json()["id"]
    client.post(f"/sessions/{sid}/sets", headers=me, json={"exercise_id": ex, "weight_kg": 50, "reps": 5})
    # unfinished sessions don't count
    assert client.get(f"/routines/{rid}", headers=me).json()["last_done_at"] is None
    client.post(f"/sessions/{sid}/finish", headers=me, json={})

    mine = next(x for x in client.get("/routines", headers=me).json() if x["id"] == rid)
    assert mine["last_done_at"] is not None
    theirs = next(x for x in client.get("/routines", headers=other).json() if x["id"] == rid)
    assert theirs["last_done_at"] is None
