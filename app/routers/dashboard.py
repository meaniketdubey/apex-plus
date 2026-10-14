from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..deps import current_user
from ..timeutil import local_date, local_today

router = APIRouter(tags=["dashboard"])

MUSCLE_GROUPS = ["legs", "chest", "back", "shoulders", "arms", "core"]


def _label(percent: int) -> str:
    if percent >= 75:
        return "High"
    if percent >= 50:
        return "Optimal"
    if percent >= 25:
        return "Recovered"
    return "Rested"


def compute_streak(workout_days: set[date], today: date) -> int:
    """Consecutive days with at least one workout. A streak is still alive if today has no workout yet."""
    day = today if today in workout_days else today - timedelta(days=1)
    streak = 0
    while day in workout_days:
        streak += 1
        day -= timedelta(days=1)
    return streak


@router.get("/dashboard", response_model=schemas.DashboardOut)
def dashboard(user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    today = local_today(user)
    week_ago = datetime.now(timezone.utc) - timedelta(days=7)

    sessions = db.scalars(
        select(models.WorkoutSession).where(
            models.WorkoutSession.user_id == user.id, models.WorkoutSession.finished_at.is_not(None)
        )
    ).all()
    workout_days = {local_date(s.started_at, user) for s in sessions}
    workouts_this_week = sum(1 for s in sessions if s.started_at.replace(tzinfo=None) >= week_ago.replace(tzinfo=None))
    minutes_today = sum(
        int((s.finished_at - s.started_at).total_seconds() // 60) for s in sessions if local_date(s.started_at, user) == today
    )

    # Relative muscle load over the last 7 days: volume (kg x reps) per muscle group.
    rows = db.execute(
        select(models.Exercise.muscle_group, models.SessionSet.weight_kg, models.SessionSet.reps)
        .join(models.SessionSet, models.SessionSet.exercise_id == models.Exercise.id)
        .join(models.WorkoutSession, models.WorkoutSession.id == models.SessionSet.session_id)
        .where(
            models.WorkoutSession.user_id == user.id,
            models.SessionSet.logged_at >= week_ago,
        )
    ).all()
    volume: dict[str, float] = defaultdict(float)
    for group, weight, reps in rows:
        volume[group] += weight * reps
    top = max(volume.values(), default=0.0)
    muscle_load = []
    for g in MUSCLE_GROUPS:
        v = volume.get(g, 0.0)
        pct = int(round(v / top * 100)) if top > 0 else 0
        muscle_load.append(schemas.MuscleLoad(muscle_group=g, volume_kg=round(v, 1), percent=pct, label=_label(pct)))

    history = db.scalars(
        select(models.BodyMetric)
        .where(models.BodyMetric.user_id == user.id)
        .order_by(models.BodyMetric.logged_on.desc(), models.BodyMetric.id.desc())
        .limit(60)
    ).all()
    water = db.scalar(
        select(models.WaterLog.litres).where(models.WaterLog.user_id == user.id, models.WaterLog.logged_on == today)
    )

    return schemas.DashboardOut(
        streak_days=compute_streak(workout_days, today),
        workouts_this_week=workouts_this_week,
        total_workouts=len(sessions),
        minutes_today=minutes_today,
        water_litres_today=water or 0.0,
        muscle_load=muscle_load,
        latest_metric=history[0] if history else None,
        weight_history=[
            schemas.WeightPoint(date=m.logged_on, weight_kg=m.weight_kg, body_fat_pct=m.body_fat_pct)
            for m in reversed(history)
        ],
    )
