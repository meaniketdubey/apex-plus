from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..ai import coach, parse
from ..ai.llm import LLM, LLMError, get_llm
from ..config import settings
from ..database import get_db
from ..deps import current_user

router = APIRouter(prefix="/ai", tags=["ai"])


@router.get("/status", response_model=schemas.AIStatusOut)
def ai_status(llm: LLM | None = Depends(get_llm), _: models.User = Depends(current_user)):
    return schemas.AIStatusOut(llm_enabled=llm is not None)


@router.post("/parse-workout", response_model=schemas.ParseWorkoutOut)
def parse_workout(
    body: schemas.ParseWorkoutIn,
    llm: LLM | None = Depends(get_llm),
    db: Session = Depends(get_db),
    _: models.User = Depends(current_user),
):
    """Free text -> proposed sets. Never saves anything: the app shows it and the user confirms."""
    exercises = list(db.scalars(select(models.Exercise)).all())
    items, source = [], "rules"
    if llm is not None:
        try:
            items = parse.llm_parse(llm, body.text, [e.name for e in exercises])
            source = "llm"
        except LLMError:
            items = []
    if not items:
        items, source = parse.rule_parse(body.text), "rules"

    out, unmatched = [], []
    for it in items:
        ex = parse.match_exercise(it.text, exercises)
        if ex is None:
            unmatched.append(it.text)
            continue
        out.append(
            schemas.ParsedItemOut(
                text=it.text,
                exercise_id=ex.id,
                exercise_name=ex.name,
                sets=[schemas.ParsedSetOut(weight_kg=s.weight_kg, reps=s.reps) for s in it.sets],
            )
        )
    return schemas.ParseWorkoutOut(items=out, unmatched=unmatched, source=source)


@router.get("/next-weight", response_model=schemas.NextWeightOut)
def next_weight(
    exercise_id: int,
    target_reps: int = Query(8, ge=1, le=50),
    user: models.User = Depends(current_user),
    db: Session = Depends(get_db),
):
    ex = db.get(models.Exercise, exercise_id)
    if ex is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Exercise not found")
    return coach.next_weight(db, user, ex, target_reps)


@router.get("/insight", response_model=schemas.InsightOut)
def insight(user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    return coach.weekly_insight(db, user)


@router.post("/generate-routine", response_model=schemas.GeneratedRoutineOut)
def generate_routine(
    body: schemas.GenerateRoutineIn,
    llm: LLM | None = Depends(get_llm),
    user: models.User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Returns a draft routine. The app lets the user edit it and then saves it through POST /routines."""
    if body.focus not in coach.FOCUS_GROUPS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"focus must be one of {sorted(coach.FOCUS_GROUPS)}")
    goal = body.goal or user.profile.goal
    equipment = body.equipment
    if equipment is None and user.profile.equipment:
        equipment = [e for e in user.profile.equipment.split(",") if e.strip()]
    draft = coach.generate_routine(db, user, goal, body.minutes, body.focus, equipment, llm)
    if not draft["items"]:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "No exercises match that focus and equipment")
    names = {e.id: e for e in db.scalars(select(models.Exercise).where(models.Exercise.id.in_([i["exercise_id"] for i in draft["items"]]))).all()}
    return schemas.GeneratedRoutineOut(
        **{k: draft[k] for k in ("title", "description", "category", "level", "est_minutes", "source")},
        items=[
            schemas.GeneratedItemOut(exercise_name=names[i["exercise_id"]].name, muscle_group=names[i["exercise_id"]].muscle_group, **i)
            for i in draft["items"]
        ],
    )


@router.post("/chat", response_model=schemas.ChatOut)
def chat(
    body: schemas.ChatIn,
    llm: LLM | None = Depends(get_llm),
    user: models.User = Depends(current_user),
    db: Session = Depends(get_db),
):
    if llm is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "The AI coach isn't set up on this server yet (missing ANTHROPIC_API_KEY).")
    if coach.chats_today(db, user) >= settings.ai_chat_daily_limit:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Daily coach message limit reached. Try again tomorrow.")
    try:
        reply = coach.chat_reply(db, user, body.message, llm)
    except LLMError:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "The coach is unavailable right now. Try again in a moment.")
    db.add(models.AIConversation(user_id=user.id, role="user", content=body.message))
    db.add(models.AIConversation(user_id=user.id, role="assistant", content=reply))
    db.commit()
    return schemas.ChatOut(reply=reply)


@router.get("/chat/history", response_model=list[schemas.ChatMessageOut])
def chat_history(limit: int = Query(50, ge=1, le=200), user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(models.AIConversation).where(models.AIConversation.user_id == user.id).order_by(models.AIConversation.id.desc()).limit(limit)
    ).all()
    return list(reversed(rows))


@router.delete("/chat/history", status_code=status.HTTP_204_NO_CONTENT)
def clear_chat_history(user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    db.execute(delete(models.AIConversation).where(models.AIConversation.user_id == user.id))
    db.commit()
