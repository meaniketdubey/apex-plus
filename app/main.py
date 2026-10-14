from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .database import Base, SessionLocal, engine
from .routers import ai, auth, challenges, circles, dashboard, metrics, progress, users, workouts
from .seed import seed


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Fail fast rather than run a real database with a guessable token-signing key.
    if settings.secret_key.startswith("dev-secret") and not settings.database_url.startswith("sqlite"):
        raise RuntimeError("Set SECRET_KEY to a long random value before using a non-SQLite database.")
    # In production run `alembic upgrade head` instead; create_all keeps first-run dev frictionless
    # and is a no-op for tables that already exist.
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed(db)
    yield


app = FastAPI(title="APEX PULSE API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for r in (auth.router, users.router, metrics.router, workouts.router, dashboard.router, circles.router, challenges.router, progress.router, ai.router):
    app.include_router(r)


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok"}
