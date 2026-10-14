"""Circle challenges: owner-created, dated competitions with their own leaderboard.

Scores come straight from finished workouts (session_sets), in each member's own time zone,
the same way streaks are computed on the dashboard.
"""
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .. import models, schemas
from ..database import get_db
from ..deps import current_user
from ..timeutil import local_date, local_today
from .circles import _member_circle, _owner_circle

router = APIRouter(tags=["challenges"])


def _longest_run(days: set[date]) -> int:
    best = run = 0
    prev: date | None = None
    for d in sorted(days):
        run = run + 1 if prev is not None and d - prev == timedelta(days=1) else 1
        best = max(best, run)
        prev = d
    return best


def _scores(db: Session, ch: models.CircleChallenge) -> list[tuple[int, str, float]]:
    """(user_id, display_name, value) for every current member."""
    members = db.execute(
        select(models.User, models.Profile.display_name)
        .join(models.CircleMember, models.CircleMember.user_id == models.User.id)
        .join(models.Profile, models.Profile.user_id == models.User.id)
        .where(models.CircleMember.circle_id == ch.circle_id)
    ).all()
    users = {u.id: u for u, _ in members}
    # A day of slack on both sides; the exact cut is made per user in their own time zone below.
    lo = datetime.combine(ch.starts_on - timedelta(days=1), time.min, tzinfo=timezone.utc)
    hi = datetime.combine(ch.ends_on + timedelta(days=2), time.min, tzinfo=timezone.utc)
    sessions = db.scalars(
        select(models.WorkoutSession)
        .where(
            models.WorkoutSession.user_id.in_(list(users)),
            models.WorkoutSession.finished_at.is_not(None),
            models.WorkoutSession.started_at >= lo,
            models.WorkoutSession.started_at < hi,
        )
        .options(selectinload(models.WorkoutSession.sets))
    ).all()

    volume: dict[int, float] = defaultdict(float)
    count: dict[int, int] = defaultdict(int)
    days: dict[int, set[date]] = defaultdict(set)
    for s in sessions:
        d = local_date(s.started_at, users[s.user_id])
        if not (ch.starts_on <= d <= ch.ends_on):
            continue
        volume[s.user_id] += sum(x.weight_kg * x.reps for x in s.sets)
        count[s.user_id] += 1
        days[s.user_id].add(d)

    out = []
    for u, name in members:
        if ch.kind == "volume":
            v = round(volume[u.id], 1)
        elif ch.kind == "workouts":
            v = float(count[u.id])
        else:
            v = float(_longest_run(days[u.id]))
        out.append((u.id, name, v))
    out.sort(key=lambda r: (-r[2], r[1].lower()))
    return out


def _status(ch: models.CircleChallenge, today: date) -> tuple[str, int]:
    if today < ch.starts_on:
        return "upcoming", (ch.starts_on - today).days
    if today > ch.ends_on:
        return "ended", 0
    return "active", (ch.ends_on - today).days + 1  # today counts


def _out(db: Session, ch: models.CircleChallenge, user: models.User, detail: bool = False):
    scores = _scores(db, ch)
    top = scores[0][2] if scores else 0.0
    rows = [
        schemas.ChallengeRow(
            rank=i + 1,
            user_id=uid,
            display_name=name,
            value=v,
            progress=round(min(1.0, v / ch.target) if ch.target else (v / top if top > 0 else 0.0), 4),
            is_me=uid == user.id,
        )
        for i, (uid, name, v) in enumerate(scores)
    ]
    me = next((r for r in rows if r.is_me), None)
    state, days_left = _status(ch, local_today(user))
    base = dict(
        id=ch.id,
        circle_id=ch.circle_id,
        title=ch.title,
        kind=ch.kind,
        target=ch.target,
        starts_on=ch.starts_on,
        ends_on=ch.ends_on,
        status=state,
        days_left=days_left,
        my_value=me.value if me else 0.0,
        my_rank=me.rank if me else None,
        leader_name=rows[0].display_name if rows and top > 0 else None,
        leader_value=top,
    )
    return schemas.ChallengeDetailOut(**base, leaderboard=rows) if detail else schemas.ChallengeOut(**base)


def _challenge(db: Session, circle_id: int, challenge_id: int) -> models.CircleChallenge:
    ch = db.get(models.CircleChallenge, challenge_id)
    if ch is None or ch.circle_id != circle_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Challenge not found")
    return ch


@router.get("/circles/{circle_id}/challenges", response_model=list[schemas.ChallengeOut])
def list_challenges(circle_id: int, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    _member_circle(db, user, circle_id)
    rows = db.scalars(
        select(models.CircleChallenge).where(models.CircleChallenge.circle_id == circle_id).order_by(models.CircleChallenge.ends_on.desc(), models.CircleChallenge.id.desc())
    ).all()
    out = [_out(db, ch, user) for ch in rows]
    order = {"active": 0, "upcoming": 1, "ended": 2}
    return sorted(out, key=lambda c: order[c.status])  # stable: keeps newest-first inside each group


@router.post("/circles/{circle_id}/challenges", response_model=schemas.ChallengeDetailOut, status_code=status.HTTP_201_CREATED)
def create_challenge(circle_id: int, body: schemas.ChallengeCreate, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    _owner_circle(db, user, circle_id)
    if body.ends_on < local_today(user) - timedelta(days=365):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "That challenge would have ended over a year ago")
    if body.kind == "streak" and body.target is not None and body.target > (body.ends_on - body.starts_on).days + 1:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "A streak target can't be longer than the challenge")
    ch = models.CircleChallenge(
        circle_id=circle_id,
        created_by=user.id,
        title=body.title.strip(),
        kind=body.kind,
        target=body.target,
        starts_on=body.starts_on,
        ends_on=body.ends_on,
    )
    db.add(ch)
    db.commit()
    db.refresh(ch)
    return _out(db, ch, user, detail=True)


@router.get("/circles/{circle_id}/challenges/{challenge_id}", response_model=schemas.ChallengeDetailOut)
def get_challenge(circle_id: int, challenge_id: int, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    _member_circle(db, user, circle_id)
    return _out(db, _challenge(db, circle_id, challenge_id), user, detail=True)


@router.delete("/circles/{circle_id}/challenges/{challenge_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_challenge(circle_id: int, challenge_id: int, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    _owner_circle(db, user, circle_id)
    db.delete(_challenge(db, circle_id, challenge_id))
    db.commit()
