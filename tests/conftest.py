import os
import tempfile

# Must be set before the app is imported so it binds to a throwaway database.
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
# Set TEST_DATABASE_URL to run the suite against Postgres (use an empty database).
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{_tmp.name}"
os.environ["SECRET_KEY"] = "test-secret"

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:  # context manager runs lifespan (create tables + seed)
        yield c


_counter = {"n": 0}


@pytest.fixture()
def auth(client):
    """A freshly registered user. Returns (headers, email)."""
    _counter["n"] += 1
    email = f"user{_counter['n']}@example.com"
    r = client.post("/auth/register", json={"email": email, "password": "supersecret1", "display_name": "Tester"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}, email
