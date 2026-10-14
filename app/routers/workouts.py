from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from .. import models, schemas
from ..database import get_db
from ..deps import current_user

router = APIRouter(tags=["training"])


def est_1rm(weight_kg: float, reps: int) -> float:
    """Epley estimate. Used to decide whether a set is a personal record."""
    return weight_kg * (1 + reps / 30)


# ------------------------------------------------------------ exercises
@router.get("/exercises", response_model=list[schemas.ExerciseOut])
def list_exercises(
    q: str | None = Query(None, max_length=60),
    muscle_group: str | None = None,
    db: Session = Depends(get_db),
    _: models.User = Depends(current_user),
):
    stmt = select(models.Exercise).order_by(models.Exercise.name)
    if q:
        stmt = stmt.where(models.Exercise.name.ilike(f"%{q}%"))
    if muscle_group:
        stmt = stmt.where(models.Exercise.muscle_group == muscle_group)
    return db.scalars(stmt).all()


@router.get("/exercises/{exercise_id}/history", response_model=schemas.ExerciseHistoryOut)
def exercise_history(
    exercise_id: int,
    exclude_session_id: int | None = None,
    user: models.User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Most recent performance of an exercise. The app shows it as ghost values while logging."""
    base = (
        select(models.SessionSet)
        .join(models.WorkoutSession)
        .where(models.WorkoutSession.user_id == user.id, models.SessionSet.exercise_id == exercise_id)
    )
    if exclude_session_id is not None:
        base = base.where(models.SessionSet.session_id != exclude_session_id)
    last = db.scalar(base.order_by(models.SessionSet.logged_at.desc(), models.SessionSet.id.desc()).limit(1))
    if last is None:
        return schemas.ExerciseHistoryOut(exercise_id=exercise_id, session_id=None, sets=[], best_est_1rm=None)
    sets = db.scalars(
        select(models.SessionSet)
        .where(models.SessionSet.session_id == last.session_id, models.SessionSet.exercise_id == exercise_id)
        .order_by(models.SessionSet.set_number)
    ).all()
    best = max((est_1rm(s.weight_kg, s.reps) for s in sets), default=None)
    return schemas.ExerciseHistoryOut(
        exercise_id=exercise_id,
        session_id=last.session_id,
        sets=sets,
        best_est_1rm=round(best, 1) if best is not None else None,
    )


# -------------------------------------------------------------- routines
def _routines_out(db: Session, user: models.User, routines: list[models.Routine]) -> list[schemas.RoutineOut]:
    """Attach `last_done_at` (the caller's own finished sessions only)."""
    ids = [r.id for r in routines]
    last = dict(
        db.execute(
            select(models.WorkoutSession.routine_id, func.max(models.WorkoutSession.finished_at))
            .where(
                models.WorkoutSession.user_id == user.id,
                models.WorkoutSession.routine_id.in_(ids),
                models.WorkoutSession.finished_at.is_not(None),
            )
            .group_by(models.WorkoutSession.routine_id)
        ).all()
    ) if ids else {}
    return [schemas.RoutineOut.model_validate(r).model_copy(update={"last_done_at": last.get(r.id)}) for r in routines]


@router.get("/routines", response_model=list[schemas.RoutineOut])
def list_routines(
    category: str | None = None,
    user: models.User = Depends(current_user),
    db: Session = Depends(get_db),
):
    stmt = (
        select(models.Routine)
        .where(or_(models.Routine.owner_id.is_(None), models.Routine.owner_id == user.id))
        .options(selectinload(models.Routine.items).selectinload(models.RoutineExercise.exercise))
        .order_by(models.Routine.id)
    )
    if category:
        stmt = stmt.where(models.Routine.category == category)
    return _routines_out(db, user, list(db.scalars(stmt).all()))


def _get_routine(db: Session, user: models.User, routine_id: int) -> models.Routine:
    routine = db.scalar(
        select(models.Routine)
        .where(models.Routine.id == routine_id)
        .options(selectinload(models.Routine.items).selectinload(models.RoutineExercise.exercise))
    )
    if routine is None or routine.owner_id not in (None, user.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Routine not found")
    return routine


@router.get("/routines/{routine_id}", response_model=schemas.RoutineOut)
def get_routine(routine_id: int, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    return _routines_out(db, user, [_get_routine(db, user, routine_id)])[0]


def _check_exercises(db: Session, items: list[schemas.RoutineItemIn]) -> None:
    ids = {i.exercise_id for i in items}
    found = set(db.scalars(select(models.Exercise.id).where(models.Exercise.id.in_(ids))).all())
    missing = ids - found
    if missing:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Unknown exercise ids: {sorted(missing)}")


def _items(items: list[schemas.RoutineItemIn]) -> list[models.RoutineExercise]:
    return [
        models.RoutineExercise(
            exercise_id=i.exercise_id, position=pos, target_sets=i.target_sets, target_reps=i.target_reps, rest_seconds=i.rest_seconds
        )
        for pos, i in enumerate(items)
    ]


@router.put("/routines/{routine_id}", response_model=schemas.RoutineOut)
def update_routine(
    routine_id: int,
    body: schemas.RoutineCreate,
    user: models.User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Replace one of your own routines. Built-in routines can't be edited (duplicate them instead)."""
    routine = _get_routine(db, user, routine_id)
    if routine.owner_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Routine not found")
    _check_exercises(db, body.items)
    routine.title = body.title
    routine.description = body.description
    routine.category = body.category
    routine.level = body.level
    routine.est_minutes = body.est_minutes
    routine.est_kcal = int(body.est_minutes * 8)
    routine.items = _items(body.items)
    db.commit()
    db.expire_all()
    return get_routine(routine_id, user, db)


@router.post("/routines/{routine_id}/duplicate", response_model=schemas.RoutineOut, status_code=status.HTTP_201_CREATED)
def duplicate_routine(routine_id: int, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    """Copy a built-in or your own routine into a new routine you own."""
    src = _get_routine(db, user, routine_id)
    copy = models.Routine(
        owner_id=user.id,
        title=f"{src.title} (copy)"[:120],
        description=src.description,
        category=src.category,
        level=src.level,
        est_minutes=src.est_minutes,
        est_kcal=src.est_kcal,
        image_url=src.image_url,
        items=[
            models.RoutineExercise(
                exercise_id=i.exercise_id, position=i.position, target_sets=i.target_sets, target_reps=i.target_reps, rest_seconds=i.rest_seconds
            )
            for i in src.items
        ],
    )
    db.add(copy)
    db.commit()
    return get_routine(copy.id, user, db)


@router.post("/routines", response_model=schemas.RoutineOut, status_code=status.HTTP_201_CREATED)
def create_routine(
    body: schemas.RoutineCreate,
    user: models.User = Depends(current_user),
    db: Session = Depends(get_db),
):
    ids = {i.exercise_id for i in body.items}
    found = set(db.scalars(select(models.Exercise.id).where(models.Exercise.id.in_(ids))).all())
    missing = ids - found
    if missing:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Unknown exercise ids: {sorted(missing)}")

    sets_total = sum(i.target_sets for i in body.items)
    routine = models.Routine(
        owner_id=user.id,
        title=body.title,
        description=body.description,
        category=body.category,
        level=body.level,
        est_minutes=body.est_minutes,
        est_kcal=int(body.est_minutes * 8),  # rough placeholder until we compute from user weight + METs
        items=[
            models.RoutineExercise(
                exercise_id=i.exercise_id,
                position=pos,
                target_sets=i.target_sets,
                target_reps=i.target_reps,
                rest_seconds=i.rest_seconds,
            )
            for pos, i in enumerate(body.items)
        ],
    )
    _ = sets_total
    db.add(routine)
    db.commit()
    return get_routine(routine.id, user, db)


@router.delete("/routines/{routine_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_routine(routine_id: int, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    routine = db.get(models.Routine, routine_id)
    if routine is None or routine.owner_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Routine not found")
    db.delete(routine)
    db.commit()


# -------------------------------------------------------------- sessions
def _get_session(db: Session, user: models.User, session_id: int) -> models.WorkoutSession:
    session = db.scalar(
        select(models.WorkoutSession)
        .where(models.WorkoutSession.id == session_id)
        .options(selectinload(models.WorkoutSession.sets))
    )
    if session is None or session.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
    return session


@router.post("/sessions", response_model=schemas.SessionOut, status_code=status.HTTP_201_CREATED)
def start_session(
    body: schemas.SessionStart,
    user: models.User = Depends(current_user),
    db: Session = Depends(get_db),
):
    title = body.title or "Workout"
    if body.routine_id is not None:
        routine = _get_routine(db, user, body.routine_id)
        title = body.title or routine.title
    session = models.WorkoutSession(user_id=user.id, routine_id=body.routine_id, title=title)
    db.add(session)
    db.commit()
    return _get_session(db, user, session.id)


@router.get("/sessions", response_model=list[schemas.SessionOut])
def list_sessions(
    limit: int = Query(20, ge=1, le=100),
    user: models.User = Depends(current_user),
    db: Session = Depends(get_db),
):
    stmt = (
        select(models.WorkoutSession)
        .where(models.WorkoutSession.user_id == user.id)
        .options(selectinload(models.WorkoutSession.sets))
        .order_by(models.WorkoutSession.started_at.desc())
        .limit(limit)
    )
    return db.scalars(stmt).all()


@router.get("/sessions/{session_id}", response_model=schemas.SessionOut)
def get_session(session_id: int, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    return _get_session(db, user, session_id)


@router.post("/sessions/{session_id}/sets", response_model=schemas.SetOut, status_code=status.HTTP_201_CREATED)
def log_set(
    session_id: int,
    body: schemas.SetIn,
    user: models.User = Depends(current_user),
    db: Session = Depends(get_db),
):
    session = _get_session(db, user, session_id)
    if body.client_id:
        # Idempotent retry: the app resends the same client_id after a dropped connection.
        existing = next((s for s in session.sets if s.client_id == body.client_id), None)
        if existing is not None:
            return existing
    if session.finished_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Session is already finished")
    if db.get(models.Exercise, body.exercise_id) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Unknown exercise")

    is_pr = False
    if body.weight_kg > 0 and body.reps > 0:
        prior = db.scalar(
            select(func.max(models.SessionSet.weight_kg * (1 + models.SessionSet.reps / 30.0)))
            .join(models.WorkoutSession)
            .where(
                models.WorkoutSession.user_id == user.id,
                models.SessionSet.exercise_id == body.exercise_id,
            )
        )
        is_pr = prior is not None and est_1rm(body.weight_kg, body.reps) > prior + 1e-9

    s = models.SessionSet(session_id=session.id, is_pr=is_pr, **body.model_dump())
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


@router.delete("/sessions/{session_id}/sets/{set_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_set(
    session_id: int,
    set_id: int,
    user: models.User = Depends(current_user),
    db: Session = Depends(get_db),
):
    session = _get_session(db, user, session_id)
    s = db.get(models.SessionSet, set_id)
    if s is None or s.session_id != session.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Set not found")
    db.delete(s)
    db.commit()


@router.post("/sessions/{session_id}/finish", response_model=schemas.SessionOut)
def finish_session(
    session_id: int,
    body: schemas.SessionFinish,
    user: models.User = Depends(current_user),
    db: Session = Depends(get_db),
):
    session = _get_session(db, user, session_id)
    if session.finished_at is None:
        if not session.sets:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "Log at least one set before finishing, or discard the workout."
            )
        session.finished_at = datetime.now(timezone.utc)
    if body.notes:
        session.notes = body.notes
    db.commit()
    return _get_session(db, user, session_id)


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def discard_session(session_id: int, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    """Throw away a workout (e.g. started by mistake). Also removes its sets."""
    session = _get_session(db, user, session_id)
    db.delete(session)
    db.commit()
