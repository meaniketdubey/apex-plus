def _user(client, email, name="Tester"):
    r = client.post("/auth/register", json={"email": email, "password": "supersecret1", "display_name": name})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _finished_session(client, h, weight=100, reps=10):
    ex = client.get("/exercises", headers=h, params={"q": "Barbell Back Squat"}).json()[0]["id"]
    sid = client.post("/sessions", headers=h, json={"title": "Leg day"}).json()["id"]
    client.post(f"/sessions/{sid}/sets", headers=h, json={"exercise_id": ex, "weight_kg": weight, "reps": reps})
    client.post(f"/sessions/{sid}/finish", headers=h, json={})
    return sid


def test_create_join_leave(client):
    owner = _user(client, "c-owner@example.com", "Owner")
    other = _user(client, "c-other@example.com", "Other")

    r = client.post("/circles", headers=owner, json={"name": "Dawn Patrol", "description": "5am lifters"})
    assert r.status_code == 201
    c = r.json()
    assert c["member_count"] == 1 and c["is_member"] and c["is_owner"]
    assert client.post("/circles", headers=other, json={"name": "Dawn Patrol"}).status_code == 409
    assert client.post("/circles", headers=other, json={"name": "ab"}).status_code == 422

    seen = [x for x in client.get("/circles", headers=other).json() if x["name"] == "Dawn Patrol"]
    assert len(seen) == 1 and seen[0]["is_member"] is False
    assert client.get("/circles", headers=other, params={"mine": True}).json() == []

    joined = client.post(f"/circles/{c['id']}/join", headers=other).json()
    assert joined["is_member"] and joined["member_count"] == 2
    assert client.post(f"/circles/{c['id']}/join", headers=other).json()["member_count"] == 2  # idempotent
    assert client.post(f"/circles/{c['id']}/leave", headers=other).status_code == 204
    assert client.get(f"/circles/{c['id']}", headers=other).json()["member_count"] == 1


def test_private_circles_are_hidden(client):
    owner = _user(client, "p-owner@example.com")
    outsider = _user(client, "p-out@example.com")
    c = client.post("/circles", headers=owner, json={"name": "Secret Squad", "is_public": False}).json()
    assert "Secret Squad" not in [x["name"] for x in client.get("/circles", headers=outsider).json()]
    assert "Secret Squad" in [x["name"] for x in client.get("/circles", headers=owner).json()]
    assert client.get(f"/circles/{c['id']}", headers=outsider).status_code == 404
    assert client.post(f"/circles/{c['id']}/join", headers=outsider).status_code == 404
    assert client.get(f"/circles/{c['id']}/feed", headers=outsider).status_code == 404


def test_feed_requires_membership_and_supports_posts_likes_comments(client):
    a = _user(client, "f-a@example.com", "Ann")
    b = _user(client, "f-b@example.com", "Ben")
    outsider = _user(client, "f-o@example.com", "Oz")
    cid = client.post("/circles", headers=a, json={"name": "Feed Club"}).json()["id"]
    client.post(f"/circles/{cid}/join", headers=b)

    assert client.get(f"/circles/{cid}/feed", headers=outsider).status_code == 403
    assert client.post(f"/circles/{cid}/posts", headers=outsider, json={"body": "hi"}).status_code == 403
    assert client.post(f"/circles/{cid}/posts", headers=a, json={"body": ""}).status_code == 422

    sid = _finished_session(client, a, 100, 10)
    post = client.post(f"/circles/{cid}/posts", headers=a, json={"body": "Leg day done", "session_id": sid}).json()
    assert post["author_name"] == "Ann" and post["is_mine"]
    assert post["workout"] == {"title": "Leg day", "sets": 1, "volume_kg": 1000.0, "pr_count": 0}

    # you cannot attach someone else's workout, or an unfinished one
    assert client.post(f"/circles/{cid}/posts", headers=b, json={"body": "x", "session_id": sid}).status_code == 422
    open_sid = client.post("/sessions", headers=b, json={}).json()["id"]
    assert client.post(f"/circles/{cid}/posts", headers=b, json={"body": "x", "session_id": open_sid}).status_code == 422

    feed = client.get(f"/circles/{cid}/feed", headers=b).json()
    assert len(feed) == 1 and feed[0]["is_mine"] is False and feed[0]["like_count"] == 0

    like = client.post(f"/posts/{post['id']}/like", headers=b).json()
    assert like == {"liked": True, "like_count": 1}
    assert client.get(f"/circles/{cid}/feed", headers=b).json()[0]["liked_by_me"] is True
    assert client.post(f"/posts/{post['id']}/like", headers=b).json() == {"liked": False, "like_count": 0}
    assert client.post(f"/posts/{post['id']}/like", headers=outsider).status_code == 403

    cm = client.post(f"/posts/{post['id']}/comments", headers=b, json={"body": "Beast!"})
    assert cm.status_code == 201 and cm.json()["author_name"] == "Ben" and cm.json()["is_mine"]
    comments = client.get(f"/posts/{post['id']}/comments", headers=a).json()
    assert [c["body"] for c in comments] == ["Beast!"] and comments[0]["is_mine"] is False
    assert client.get(f"/circles/{cid}/feed", headers=a).json()[0]["comment_count"] == 1
    assert client.get(f"/posts/{post['id']}/comments", headers=outsider).status_code == 403

    # only the author can delete
    assert client.delete(f"/posts/{post['id']}", headers=b).status_code == 404
    assert client.delete(f"/posts/{post['id']}", headers=a).status_code == 204
    assert client.get(f"/circles/{cid}/feed", headers=a).json() == []


def test_feed_pagination(client):
    a = _user(client, "pg-a@example.com")
    cid = client.post("/circles", headers=a, json={"name": "Paging Club"}).json()["id"]
    ids = [client.post(f"/circles/{cid}/posts", headers=a, json={"body": f"post {i}"}).json()["id"] for i in range(5)]
    page1 = client.get(f"/circles/{cid}/feed", headers=a, params={"limit": 2}).json()
    assert [p["id"] for p in page1] == [ids[4], ids[3]]  # newest first
    page2 = client.get(f"/circles/{cid}/feed", headers=a, params={"limit": 2, "before_id": page1[-1]["id"]}).json()
    assert [p["id"] for p in page2] == [ids[2], ids[1]]


def test_leaderboard_ranks_by_volume(client):
    a = _user(client, "l-a@example.com", "Ann")
    b = _user(client, "l-b@example.com", "Ben")
    c = _user(client, "l-c@example.com", "Cy")  # member who never trains
    outsider = _user(client, "l-o@example.com", "Oz")
    cid = client.post("/circles", headers=a, json={"name": "Lift Board"}).json()["id"]
    client.post(f"/circles/{cid}/join", headers=b)
    client.post(f"/circles/{cid}/join", headers=c)
    _finished_session(client, a, 100, 10)  # 1000
    _finished_session(client, b, 120, 10)  # 1200
    _finished_session(client, outsider, 500, 10)  # not in the circle, must not appear

    board = client.get(f"/circles/{cid}/leaderboard", headers=a).json()
    assert [r["display_name"] for r in board] == ["Ben", "Ann", "Cy"]
    assert [r["rank"] for r in board] == [1, 2, 3]
    assert board[0]["volume_kg"] == 1200.0 and board[0]["workouts"] == 1
    assert board[2]["workouts"] == 0 and board[2]["volume_kg"] == 0
    assert [r["is_me"] for r in board] == [False, True, False]
    assert client.get(f"/circles/{cid}/leaderboard", headers=outsider).status_code == 403
