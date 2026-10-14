"""Training progress derived from session_sets: per-exercise history, personal records, period summaries.

Nothing here is stored; it's all computed from the user's own logged sets, in their time zone.
"""
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from .. import models, schemas
from ..database import get_db
from ..deps import current_user
from ..timeutil import local_date, local_today
from .workouts import est_1rm

router = APIRouter(prefix="/progress", tags=["progress"])

RECENT_DAYS = 14  # records newer than this get a "NEW" badge


def _since(days: int) -> datetime:
    return datetime.combine(date.today() - timedelta(days=days + 1), time.min, tzinfo=timezone.utc)


@router.get("/exercises", response_model=list[schemas.LoggedExerciseOut])
def logged_exercises(user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    """Exercises you have logged at least one working set for, most trained first."""
    rows = db.execute(
        select(
            models.Exercise.id,
            models.Exercise.name,
            models.Exercise.muscle_group,
            func.count(func.distinct(models.SessionSet.session_id)),
            func.max(models.SessionSet.logged_at),
        )
        .join(models.SessionSet, models.SessionSet.exercise_id == models.Exercise.id)
        .join(models.WorkoutSession, models.WorkoutSession.id == models.SessionSet.session_id)
        .where(models.WorkoutSession.user_id == user.id, models.SessionSet.reps > 0)
        .group_by(models.Exercise.id, models.Exercise.name, models.Exercise.muscle_group)
        .order_by(func.count(func.distinct(models.SessionSet.session_id)).desc(), models.Exercise.name)
    ).all()
    return [
        schemas.LoggedExerciseOut(id=i, name=n, muscle_group=g, sessions=c, last_logged_at=last) for i, n, g, c, last in rows
    ]


@router.get("/exercises/{exercise_id}", response_model=schemas.ExerciseProgressOut)
def exercise_progress(
    exercise_id: int,
    days: int = Query(365, ge=1, le=3650),
    user: models.User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """One point per training day: best estimated 1RM, the set that produced it, top weight and volume."""
    ex = db.get(models.Exercise, exercise_id)
    if ex is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Exercise not found")
    rows = db.execute(
        select(models.SessionSet, models.WorkoutSession.started_at)
        .join(models.WorkoutSession, models.WorkoutSession.id == models.SessionSet.session_id)
        .where(
            models.WorkoutSession.user_id == user.id,
            models.SessionSet.exercise_id == exercise_id,
            models.SessionSet.reps > 0,
            models.WorkoutSession.started_at >= _since(days),
        )
    ).all()
    cutoff = local_today(user) - timedelta(days=days - 1)
    by_day: dict[date, list[models.SessionSet]] = defaultdict(list)
    for s, started in rows:
        d = local_date(started, user)
        if d >= cutoff:
            by_day[d].append(s)
    points = []
    for d in sorted(by_day):
        sets = by_day[d]
        best = max(sets, key=lambda x: (est_1rm(x.weight_kg, x.reps), x.weight_kg))
        points.append(
            schemas.ExercisePoint(
                date=d,
                best_est_1rm=round(est_1rm(best.weight_kg, best.reps), 1),
                best_weight_kg=best.weight_kg,
                best_reps=best.reps,
                top_weight_kg=max(x.weight_kg for x in sets),
                volume_kg=round(sum(x.weight_kg * x.reps for x in sets), 1),
            )
        )
    return schemas.ExerciseProgressOut(exercise_id=ex.id, name=ex.name, muscle_group=ex.muscle_group, points=points)


@router.get("/records", response_model=list[schemas.RecordOut])
def personal_records(user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    """Best estimated 1RM per exercise (earliest date it was reached), newest records first."""
    rows = db.execute(
        select(models.SessionSet, models.WorkoutSession.started_at, models.Exercise.name, models.Exercise.muscle_group)
        .join(models.WorkoutSession, models.WorkoutSession.id == models.SessionSet.session_id)
        .join(models.Exercise, models.Exercise.id == models.SessionSet.exercise_id)
        .where(models.WorkoutSession.user_id == user.id, models.SessionSet.weight_kg > 0, models.SessionSet.reps > 0)
        .order_by(models.SessionSet.logged_at, models.SessionSet.id)
    ).all()
    best: dict[int, tuple[float, models.SessionSet, datetime, str, str]] = {}
    for s, started, name, group in rows:
        e = est_1rm(s.weight_kg, s.reps)
        if s.exercise_id not in best or e > best[s.exercise_id][0] + 1e-9:  # strictly better: keeps the first time it was hit
            best[s.exercise_id] = (e, s, started, name, group)
    today = local_today(user)
    out = []
    for ex_id, (e, s, started, name, group) in best.items():
        on = local_date(started, user)
        out.append(
            schemas.RecordOut(
                exercise_id=ex_id,
                name=name,
                muscle_group=group,
                est_1rm=round(e, 1),
                weight_kg=s.weight_kg,
                reps=s.reps,
                achieved_on=on,
                is_recent=(today - on).days < RECENT_DAYS,
            )
        )
    out.sort(key=lambda r: (r.achieved_on, r.est_1rm), reverse=True)
    return out


def _totals(sessions: list[models.WorkoutSession]) -> schemas.PeriodTotals:
    minutes = 0
    for s in sessions:
        if s.finished_at is not None:
            minutes += max(0, int((s.finished_at - s.started_at).total_seconds() // 60))
    return schemas.PeriodTotals(
        workouts=len(sessions),
        volume_kg=round(sum(x.weight_kg * x.reps for s in sessions for x in s.sets), 1),
        active_minutes=minutes,
    )


def _pct(now: float, before: float) -> float | None:
    if before == 0:
        return None
    return round((now - before) / before * 100, 1)


@router.get("/summary", response_model=schemas.SummaryOut)
def summary(
    period: str = Query("week", pattern="^(week|month)$"),
    user: models.User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """The last 7 (or 30) days including today, against the 7 (or 30) days before that. Finished workouts only."""
    n = 7 if period == "week" else 30
    today = local_today(user)
    cur_start = today - timedelta(days=n - 1)
    prev_start = cur_start - timedelta(days=n)
    sessions = db.scalars(
        select(models.WorkoutSession)
        .where(
            models.WorkoutSession.user_id == user.id,
            models.WorkoutSession.finished_at.is_not(None),
            models.WorkoutSession.started_at >= _since(2 * n),
        )
        .options(selectinload(models.WorkoutSession.sets))
    ).all()
    cur, prev = [], []
    for s in sessions:
        d = local_date(s.started_at, user)
        if cur_start <= d <= today:
            cur.append(s)
        elif prev_start <= d < cur_start:
            prev.append(s)
    c, p = _totals(cur), _totals(prev)
    return schemas.SummaryOut(
        period=period,
        days=n,
        start=cur_start,
        end=today,
        current=c,
        previous=p,
        change=schemas.PeriodChange(
            workouts_pct=_pct(c.workouts, p.workouts),
            volume_pct=_pct(c.volume_kg, p.volume_kg),
            minutes_pct=_pct(c.active_minutes, p.active_minutes),
        ),
    )
