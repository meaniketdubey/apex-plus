"""Training intelligence. Numbers (next weight, volume, PRs) are always computed by plain code;
the LLM, when present, only chooses from the exercise library or talks about numbers it was given."""
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import models
from ..routers.dashboard import MUSCLE_GROUPS, compute_streak
from ..timeutil import local_date, local_today
from .llm import LLM, LLMError

LEG_COMPOUND_STEP = 5.0
DEFAULT_STEP = 2.5


def _round_to(x: float, step: float = 2.5) -> float:
    return round(round(x / step) * step, 2)


# ------------------------------------------------------------ next weight
def next_weight(db: Session, user: models.User, exercise: models.Exercise, target_reps: int = 8) -> dict:
    """Double-progression rule of thumb: hit your target reps on every working set -> add weight."""
    last = db.scalar(
        select(models.SessionSet)
        .join(models.WorkoutSession)
        .where(models.WorkoutSession.user_id == user.id, models.SessionSet.exercise_id == exercise.id)
        .order_by(models.SessionSet.logged_at.desc(), models.SessionSet.id.desc())
        .limit(1)
    )
    base = {"exercise_id": exercise.id, "exercise_name": exercise.name, "target_reps": target_reps}
    if last is None:
        return {**base, "suggested_weight_kg": None, "last_weight_kg": None, "reason": "No history for this exercise yet. Start light and focus on clean reps."}

    sets = db.scalars(
        select(models.SessionSet)
        .where(models.SessionSet.session_id == last.session_id, models.SessionSet.exercise_id == exercise.id)
        .order_by(models.SessionSet.set_number)
    ).all()
    top = max(s.weight_kg for s in sets)
    at_top = [s for s in sets if s.weight_kg == top]

    if top == 0:  # bodyweight movement: progress by reps, not load
        best = max(s.reps for s in sets)
        return {**base, "suggested_weight_kg": 0.0, "last_weight_kg": 0.0, "reason": f"Bodyweight movement. Last time your best set was {best} reps; aim for {best + 1}-{best + 2}."}

    step = LEG_COMPOUND_STEP if (exercise.muscle_group == "legs" and exercise.is_compound) else DEFAULT_STEP
    if all(s.reps >= target_reps for s in at_top):
        return {**base, "suggested_weight_kg": _round_to(top + step, step), "last_weight_kg": top, "reason": f"You hit {target_reps}+ reps on every set at {top:g} kg. Add {step:g} kg."}

    worst = min(s.reps for s in at_top)
    if worst < target_reps - 3:
        lighter = _round_to(top * 0.95)
        return {**base, "suggested_weight_kg": lighter, "last_weight_kg": top, "reason": f"One set dropped to {worst} reps at {top:g} kg. Take a small step back to {lighter:g} kg and rebuild."}
    return {**base, "suggested_weight_kg": top, "last_weight_kg": top, "reason": f"Stay at {top:g} kg until every set reaches {target_reps} reps (your lowest was {worst})."}


# ---------------------------------------------------------------- insight
def _window_stats(db: Session, user: models.User, start: datetime, end: datetime) -> dict:
    sessions = db.scalars(
        select(models.WorkoutSession).where(
            models.WorkoutSession.user_id == user.id,
            models.WorkoutSession.finished_at.is_not(None),
            models.WorkoutSession.finished_at >= start,
            models.WorkoutSession.finished_at < end,
        )
    ).all()
    rows = db.execute(
        select(models.Exercise.muscle_group, models.SessionSet.weight_kg, models.SessionSet.reps, models.SessionSet.is_pr)
        .join(models.SessionSet, models.SessionSet.exercise_id == models.Exercise.id)
        .join(models.WorkoutSession, models.WorkoutSession.id == models.SessionSet.session_id)
        .where(
            models.WorkoutSession.user_id == user.id,
            models.WorkoutSession.finished_at.is_not(None),
            models.WorkoutSession.finished_at >= start,
            models.WorkoutSession.finished_at < end,
        )
    ).all()
    by_group: dict[str, float] = {g: 0.0 for g in MUSCLE_GROUPS}
    for group, w, r, _ in rows:
        by_group[group] = by_group.get(group, 0.0) + w * r
    return {
        "workouts": len(sessions),
        "volume_kg": round(sum(by_group.values()), 1),
        "prs": sum(1 for r in rows if r[3]),
        "by_group": {k: round(v, 1) for k, v in by_group.items()},
    }


def weekly_insight(db: Session, user: models.User) -> dict:
    now = datetime.now(timezone.utc)
    cur = _window_stats(db, user, now - timedelta(days=7), now + timedelta(minutes=1))
    prev = _window_stats(db, user, now - timedelta(days=14), now - timedelta(days=7))
    goal_days = user.profile.days_per_week

    days = {
        local_date(s.started_at, user)
        for s in db.scalars(select(models.WorkoutSession).where(models.WorkoutSession.user_id == user.id, models.WorkoutSession.finished_at.is_not(None))).all()
    }
    streak = compute_streak(days, local_today(user))

    bullets: list[str] = []
    if cur["workouts"] == 0 and prev["workouts"] == 0:
        headline = "No workouts logged in the last two weeks"
        bullets.append("Pick a routine and log one session this week to get your trends going.")
    else:
        headline = f"{cur['workouts']} of {goal_days} planned workouts this week"
        if cur["workouts"] >= goal_days:
            bullets.append("You hit your weekly target. Keep recovery and sleep in check.")
        elif cur["workouts"] > 0:
            left = goal_days - cur["workouts"]
            bullets.append(f"{left} more session{'s' if left != 1 else ''} to reach your weekly target.")
        if prev["volume_kg"] > 0 and cur["volume_kg"] > 0:
            change = (cur["volume_kg"] - prev["volume_kg"]) / prev["volume_kg"] * 100
            direction = "up" if change >= 0 else "down"
            bullets.append(f"Training volume is {direction} {abs(change):.0f}% on last week ({cur['volume_kg']:,.0f} kg vs {prev['volume_kg']:,.0f} kg).")
        elif cur["volume_kg"] > 0:
            bullets.append(f"{cur['volume_kg']:,.0f} kg total volume this week.")
        if cur["prs"]:
            bullets.append(f"{cur['prs']} new personal record{'s' if cur['prs'] != 1 else ''} this week.")
        trained = {g: v for g, v in cur["by_group"].items() if v > 0}
        if trained:
            top = max(trained, key=trained.get)
            bullets.append(f"Most work went into {top}.")
            missing = [g for g in MUSCLE_GROUPS if cur["by_group"].get(g, 0) == 0]
            if missing:
                bullets.append(f"Nothing logged for {', '.join(missing[:3])} yet this week.")
    if streak >= 3:
        bullets.append(f"{streak}-day streak. Nice consistency.")
    return {"headline": headline, "bullets": bullets, "stats": {"this_week": cur, "last_week": prev, "streak_days": streak}}


# ------------------------------------------------------- routine generator
FOCUS_GROUPS = {
    "full body": ["legs", "chest", "back", "shoulders", "arms", "core"],
    "upper": ["chest", "back", "shoulders", "arms"],
    "lower": ["legs", "core"],
    "push": ["chest", "shoulders", "arms"],
    "pull": ["back", "arms"],
    "legs": ["legs", "core"],
}
GOAL_SCHEME = {  # sets, reps, rest seconds for compounds / accessories
    "Strength": {"c": (4, 5, 150), "a": (3, 8, 90)},
    "Muscle Building": {"c": (4, 8, 90), "a": (3, 12, 60)},
    "Fat Loss": {"c": (3, 12, 60), "a": (3, 15, 45)},
    "General Fitness": {"c": (3, 10, 75), "a": (3, 12, 60)},
}
CATEGORY_FOR_GOAL = {"Strength": "Strength", "Muscle Building": "Hypertrophy", "Fat Loss": "Conditioning", "General Fitness": "General"}


def _scheme(goal: str, compound: bool) -> tuple[int, int, int]:
    return GOAL_SCHEME.get(goal, GOAL_SCHEME["General Fitness"])["c" if compound else "a"]


def _clean_equipment(equipment: list[str] | None) -> set[str] | None:
    """None = no restriction. Bodyweight moves are always allowed."""
    cleaned = {e.strip().lower() for e in (equipment or []) if e.strip()}
    return cleaned | {"bodyweight"} if cleaned else None


def _deterministic_routine(exercises: list[models.Exercise], goal: str, minutes: int, focus: str, equipment: set[str] | None) -> dict:
    groups = FOCUS_GROUPS.get(focus, FOCUS_GROUPS["full body"])
    pool = [e for e in exercises if e.muscle_group in groups and (equipment is None or e.equipment in equipment)]
    count = max(3, min(8, minutes // 9))
    # compounds first, then accessories; round-robin over muscle groups so one group doesn't dominate
    ordered: list[models.Exercise] = []
    for compound in (True, False):
        buckets = {g: [e for e in pool if e.muscle_group == g and e.is_compound == compound] for g in groups}
        while any(buckets.values()):
            for g in groups:
                if buckets[g]:
                    ordered.append(buckets[g].pop(0))
    picked = ordered[:count]
    items = []
    for e in picked:
        sets, reps, rest = _scheme(goal, e.is_compound)
        items.append({"exercise_id": e.id, "target_sets": sets, "target_reps": reps, "rest_seconds": rest})
    return {
        "title": f"{focus.title()} · {goal}",
        "description": f"{len(items)} exercises, about {minutes} minutes. Compound lifts first, then accessories.",
        "category": CATEGORY_FOR_GOAL.get(goal, "General"),
        "level": "Intermediate",
        "est_minutes": minutes,
        "items": items,
    }


def generate_routine(
    db: Session, user: models.User, goal: str, minutes: int, focus: str, equipment: list[str] | None, llm: LLM | None
) -> dict:
    exercises = list(db.scalars(select(models.Exercise).order_by(models.Exercise.id)).all())
    eq = _clean_equipment(equipment)
    fallback = _deterministic_routine(exercises, goal, minutes, focus, eq)
    if not fallback["items"]:
        return {**fallback, "source": "rules"}

    if llm is not None:
        allowed = [e for e in exercises if eq is None or e.equipment in eq]
        catalogue = "\n".join(f"{e.id}|{e.name}|{e.muscle_group}|{'compound' if e.is_compound else 'isolation'}" for e in allowed)
        system = (
            "You design gym routines. Reply with ONLY a JSON object: "
            '{"title": string, "description": string, "items": [{"exercise_id": int, "target_sets": int, "target_reps": int, "rest_seconds": int}]}. '
            "Use only exercise_id values from the catalogue. Compound lifts first. No more exercises than the requested count."
        )
        user_msg = (
            f"Goal: {goal}\nFocus: {focus}\nTime available: {minutes} minutes\nExercises wanted: {len(fallback['items'])}\n"
            f"Catalogue (id|name|muscle|type):\n{catalogue}"
        )
        try:
            raw = llm.complete(system, [{"role": "user", "content": user_msg}], max_tokens=900)
            data = json.loads(raw[raw.find("{") : raw.rfind("}") + 1])
            valid = {e.id for e in allowed}
            items, seen = [], set()
            for it in data.get("items", [])[: len(fallback["items"])]:
                eid = int(it["exercise_id"])
                if eid not in valid or eid in seen:
                    continue
                seen.add(eid)
                items.append(
                    {
                        "exercise_id": eid,
                        "target_sets": max(1, min(10, int(it.get("target_sets", 3)))),
                        "target_reps": max(1, min(50, int(it.get("target_reps", 10)))),
                        "rest_seconds": max(15, min(300, int(it.get("rest_seconds", 60)))),
                    }
                )
            if len(items) >= 3:
                return {**fallback, "title": str(data.get("title") or fallback["title"])[:120], "description": str(data.get("description") or fallback["description"])[:300], "items": items, "source": "llm"}
        except (LLMError, ValueError, KeyError, TypeError, AttributeError):
            pass  # fall through to the rule-based routine
    return {**fallback, "source": "rules"}


# -------------------------------------------------------------------- chat
COACH_SYSTEM = (
    "You are the coach inside the APEX PULSE fitness app. Be concise, practical and encouraging. "
    "Base answers on the user's data below and say so when the data is missing instead of guessing. "
    "You are not a doctor: for pain, injury, medical conditions, eating disorders or medication, "
    "tell the user to see a qualified professional. Do not give extreme diet or training advice. "
    "Keep replies under 180 words unless asked for more."
)


def build_context(db: Session, user: models.User) -> str:
    p = user.profile
    insight = weekly_insight(db, user)
    sessions = db.scalars(
        select(models.WorkoutSession)
        .where(models.WorkoutSession.user_id == user.id, models.WorkoutSession.finished_at.is_not(None))
        .order_by(models.WorkoutSession.started_at.desc())
        .limit(5)
    ).all()
    lines = [
        f"Name: {p.display_name}. Goal: {p.goal}. Style: {p.discipline}. Trains {p.days_per_week} days/week.",
        f"Height: {p.height_cm} cm. Weight: {p.weight_kg} kg.",
        f"This week: {insight['headline']}. " + " ".join(insight["bullets"]),
        "Recent workouts:",
    ]
    for s in sessions:
        by_ex: dict[str, list[str]] = {}
        for st in s.sets:
            by_ex.setdefault(st.exercise.name, []).append(f"{st.weight_kg:g}x{st.reps}")
        detail = "; ".join(f"{n}: {', '.join(v)}" for n, v in by_ex.items())
        lines.append(f"- {local_date(s.started_at, user)} {s.title}: {detail}")
    metrics = db.scalars(select(models.BodyMetric).where(models.BodyMetric.user_id == user.id).order_by(models.BodyMetric.logged_on.desc()).limit(4)).all()
    if metrics:
        lines.append("Recent body metrics (newest first): " + "; ".join(f"{m.logged_on}: {m.weight_kg} kg, {m.body_fat_pct}% bf" for m in metrics))
    return "\n".join(lines)


def chat_reply(db: Session, user: models.User, message: str, llm: LLM) -> str:
    history = db.scalars(
        select(models.AIConversation).where(models.AIConversation.user_id == user.id).order_by(models.AIConversation.id.desc()).limit(10)
    ).all()[::-1]
    messages = [{"role": m.role, "content": m.content} for m in history if m.role in ("user", "assistant")]
    messages.append({"role": "user", "content": message})
    # The API requires alternating roles starting with "user"; drop anything that breaks that.
    clean: list[dict] = []
    for m in messages:
        if clean and clean[-1]["role"] == m["role"]:
            clean[-1] = m
        else:
            clean.append(m)
    while clean and clean[0]["role"] != "user":
        clean.pop(0)
    system = COACH_SYSTEM + "\n\nUSER DATA\n" + build_context(db, user)
    return llm.complete(system, clean, max_tokens=500)


def chats_today(db: Session, user: models.User) -> int:
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return db.scalar(
        select(func.count()).select_from(models.AIConversation).where(
            models.AIConversation.user_id == user.id, models.AIConversation.role == "user", models.AIConversation.created_at >= start
        )
    ) or 0
