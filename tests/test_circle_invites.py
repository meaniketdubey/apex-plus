"""Circle invites, privacy and owner-only management."""
import pytest

from app.ratelimit import invite_code_limiter

from test_circles import _finished_session, _user


@pytest.fixture(autouse=True)
def _fresh_limiter():
    invite_code_limiter.reset()
    yield
    invite_code_limiter.reset()


def _private(client, owner, name):
    r = client.post("/circles", headers=owner, json={"name": name, "description": "secret", "is_public": False})
    assert r.status_code == 201, r.text
    return r.json()


def test_creator_is_owner_and_only_members_see_invite_code(client):
    owner = _user(client, "i-own@example.com", "Owner")
    other = _user(client, "i-oth@example.com", "Other")
    pub = client.post("/circles", headers=owner, json={"name": "Open Gym"}).json()
    assert pub["is_owner"] and pub["role"] == "owner" and len(pub["invite_code"]) >= 8
    assert pub["members_preview"] == ["Owner"] and pub["last_activity_at"] is not None

    as_outsider = client.get(f"/circles/{pub['id']}", headers=other).json()
    assert as_outsider["invite_code"] is None and as_outsider["role"] is None
    listed = [c for c in client.get("/circles", headers=other).json() if c["id"] == pub["id"]][0]
    assert listed["invite_code"] is None

    joined = client.post(f"/circles/{pub['id']}/join", headers=other).json()
    assert joined["role"] == "member" and not joined["is_owner"] and joined["invite_code"] == pub["invite_code"]


def test_private_circle_invisible_and_joinable_only_by_code(client):
    owner = _user(client, "pv-own@example.com", "Owner")
    outsider = _user(client, "pv-out@example.com", "Outsider")
    c = _private(client, owner, "Night Shift")

    assert c["id"] not in [x["id"] for x in client.get("/circles", headers=outsider).json()]
    assert client.get(f"/circles/{c['id']}", headers=outsider).status_code == 404
    assert client.get(f"/circles/{c['id']}/members", headers=outsider).status_code == 404
    assert client.post(f"/circles/{c['id']}/join", headers=outsider).status_code == 404

    preview = client.get("/circles/join-preview", headers=outsider, params={"code": c["invite_code"]})
    assert preview.status_code == 200
    assert preview.json() == {"id": c["id"], "name": "Night Shift", "description": "secret", "member_count": 1, "is_public": False, "is_member": False}

    joined = client.post("/circles/join-by-code", headers=outsider, json={"code": c["invite_code"]})
    assert joined.status_code == 200 and joined.json()["is_member"] and joined.json()["member_count"] == 2
    # idempotent
    again = client.post("/circles/join-by-code", headers=outsider, json={"code": c["invite_code"]})
    assert again.status_code == 200 and again.json()["member_count"] == 2
    assert client.get(f"/circles/{c['id']}/feed", headers=outsider).status_code == 200


def test_wrong_code_reveals_nothing(client):
    user = _user(client, "wc-user@example.com")
    for code in ["doesnotexist", "AAAAAAAAAAA"]:
        p = client.get("/circles/join-preview", headers=user, params={"code": code})
        j = client.post("/circles/join-by-code", headers=user, json={"code": code})
        assert p.status_code == 404 and j.status_code == 404
        assert p.json() == j.json() == {"detail": "This invite link is invalid or has expired."}
    assert client.get("/circles/join-preview", headers=user, params={"code": "x"}).status_code == 422
    assert client.get("/circles/join-preview", params={"code": "whatever"}).status_code == 401


def test_reset_code_kills_old_link(client):
    owner = _user(client, "rs-own@example.com")
    late = _user(client, "rs-late@example.com")
    c = _private(client, owner, "Reset Crew")
    old = c["invite_code"]
    new = client.post(f"/circles/{c['id']}/invite-code/reset", headers=owner).json()["invite_code"]
    assert new != old
    assert client.post("/circles/join-by-code", headers=late, json={"code": old}).status_code == 404
    assert client.get("/circles/join-preview", headers=late, params={"code": old}).status_code == 404
    assert client.post("/circles/join-by-code", headers=late, json={"code": new}).status_code == 200


def test_join_by_code_is_rate_limited_per_user(client):
    guesser = _user(client, "rl-guess@example.com")
    honest = _user(client, "rl-honest@example.com")
    owner = _user(client, "rl-own@example.com")
    c = _private(client, owner, "Rate Limited")
    codes = [client.post("/circles/join-by-code", headers=guesser, json={"code": f"guess{i:04d}"}).status_code for i in range(12)]
    assert codes[:10] == [404] * 10 and codes[10:] == [429, 429]
    # even the right code is refused while over budget; the preview shares the budget
    assert client.post("/circles/join-by-code", headers=guesser, json={"code": c["invite_code"]}).status_code == 429
    assert client.get("/circles/join-preview", headers=guesser, params={"code": c["invite_code"]}).status_code == 429
    # other users are unaffected
    assert client.post("/circles/join-by-code", headers=honest, json={"code": c["invite_code"]}).status_code == 200


def test_owner_edits_and_non_owner_cannot(client):
    owner = _user(client, "ed-own@example.com")
    member = _user(client, "ed-mem@example.com")
    outsider = _user(client, "ed-out@example.com")
    c = client.post("/circles", headers=owner, json={"name": "Edit Me"}).json()
    client.post(f"/circles/{c['id']}/join", headers=member)

    r = client.patch(f"/circles/{c['id']}", headers=owner, json={"name": "Edited", "description": "new", "is_public": False})
    assert r.status_code == 200 and r.json()["name"] == "Edited" and r.json()["is_public"] is False
    assert client.patch(f"/circles/{c['id']}", headers=owner, json={"name": "ab"}).status_code == 422
    client.post("/circles", headers=outsider, json={"name": "Taken Name"})
    assert client.patch(f"/circles/{c['id']}", headers=owner, json={"name": "Taken Name"}).status_code == 409

    assert client.patch(f"/circles/{c['id']}", headers=member, json={"name": "Hijack"}).status_code == 403
    assert client.post(f"/circles/{c['id']}/invite-code/reset", headers=member).status_code == 403
    assert client.delete(f"/circles/{c['id']}", headers=member).status_code == 403
    # now private: outsiders get 404, not 403
    assert client.patch(f"/circles/{c['id']}", headers=outsider, json={"name": "Hijack"}).status_code == 404

    # back to public: it shows up in Discover for outsiders
    client.patch(f"/circles/{c['id']}", headers=owner, json={"is_public": True})
    assert c["id"] in [x["id"] for x in client.get("/circles", headers=outsider).json()]


def test_members_list_add_by_email_and_remove(client):
    owner = _user(client, "mb-own@example.com", "Olivia")
    bob = _user(client, "mb-bob@example.com", "Bob")
    member = _user(client, "mb-mem@example.com", "Mia")
    c = _private(client, owner, "Members Only")

    yes = client.post(f"/circles/{c['id']}/members", headers=owner, json={"email": "MB-BOB@example.com"})
    no = client.post(f"/circles/{c['id']}/members", headers=owner, json={"email": "nobody-here@example.com"})
    # no account enumeration: identical status and body either way
    assert yes.status_code == no.status_code == 202 and yes.json() == no.json()
    again = client.post(f"/circles/{c['id']}/members", headers=owner, json={"email": "mb-bob@example.com"})
    assert again.status_code == 202 and again.json() == yes.json()
    assert client.post(f"/circles/{c['id']}/members", headers=owner, json={"email": "not-an-email"}).status_code == 422

    client.post("/circles/join-by-code", headers=member, json={"code": c["invite_code"]})
    rows = client.get(f"/circles/{c['id']}/members", headers=bob).json()
    assert [(r["display_name"], r["role"]) for r in rows] == [("Olivia", "owner"), ("Bob", "member"), ("Mia", "member")]
    assert [r["is_me"] for r in rows] == [False, True, False] and all(r["joined_at"] for r in rows)

    bob_id = rows[1]["user_id"]
    mia_id = rows[2]["user_id"]
    # members cannot add or remove
    assert client.post(f"/circles/{c['id']}/members", headers=member, json={"email": "x@example.com"}).status_code == 403
    assert client.delete(f"/circles/{c['id']}/members/{bob_id}", headers=member).status_code == 403
    assert client.delete(f"/circles/{c['id']}/members/{bob_id}", headers=owner).status_code == 204
    assert client.delete(f"/circles/{c['id']}/members/{bob_id}", headers=owner).status_code == 404
    assert client.get(f"/circles/{c['id']}", headers=bob).status_code == 404  # private and no longer a member
    # a member can leave on their own
    assert client.post(f"/circles/{c['id']}/leave", headers=member).status_code == 204
    assert mia_id not in [r["user_id"] for r in client.get(f"/circles/{c['id']}/members", headers=owner).json()]


def test_owner_cannot_leave_or_remove_self_until_transfer(client):
    owner = _user(client, "tr-own@example.com", "Olga")
    member = _user(client, "tr-mem@example.com", "Max")
    c = client.post("/circles", headers=owner, json={"name": "Handover"}).json()
    client.post(f"/circles/{c['id']}/join", headers=member)
    owner_id = client.get("/me", headers=owner).json()["id"]
    member_id = client.get("/me", headers=member).json()["id"]

    assert client.post(f"/circles/{c['id']}/leave", headers=owner).status_code == 409
    assert client.delete(f"/circles/{c['id']}/members/{owner_id}", headers=owner).status_code == 409
    assert client.post(f"/circles/{c['id']}/transfer", headers=member, json={"user_id": member_id}).status_code == 403
    assert client.post(f"/circles/{c['id']}/transfer", headers=owner, json={"user_id": 999999}).status_code == 404

    t = client.post(f"/circles/{c['id']}/transfer", headers=owner, json={"user_id": member_id})
    assert t.status_code == 200 and t.json()["is_owner"] is False and t.json()["role"] == "member"
    assert client.get(f"/circles/{c['id']}", headers=member).json()["is_owner"] is True
    assert client.post(f"/circles/{c['id']}/leave", headers=owner).status_code == 204


def test_delete_circle_cascades(client):
    owner = _user(client, "dl-own@example.com")
    member = _user(client, "dl-mem@example.com")
    c = client.post("/circles", headers=owner, json={"name": "Short Lived"}).json()
    client.post(f"/circles/{c['id']}/join", headers=member)
    sid = _finished_session(client, owner)
    post = client.post(f"/circles/{c['id']}/posts", headers=owner, json={"body": "hi", "session_id": sid}).json()
    client.post(f"/posts/{post['id']}/like", headers=member)
    client.post(f"/posts/{post['id']}/comments", headers=member, json={"body": "yo"})

    assert client.delete(f"/circles/{c['id']}", headers=member).status_code == 403
    assert client.delete(f"/circles/{c['id']}", headers=owner).status_code == 204
    assert client.get(f"/circles/{c['id']}", headers=owner).status_code == 404
    assert client.get(f"/posts/{post['id']}/comments", headers=owner).status_code == 404
    assert c["id"] not in [x["id"] for x in client.get("/circles", headers=member, params={"mine": True}).json()]
    # the attached workout itself is untouched
    assert client.get(f"/sessions/{sid}", headers=owner).status_code == 200
    # the name is free again
    assert client.post("/circles", headers=owner, json={"name": "Short Lived"}).status_code == 201
