"""Seed built-in exercises, routines and achievements. Safe to run repeatedly."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models

# (name, muscle_group, equipment, is_compound)
EXERCISES = [
    ("Barbell Back Squat", "legs", "barbell", True),
    ("Romanian Deadlift", "legs", "barbell", True),
    ("Bulgarian Split Squat", "legs", "dumbbell", True),
    ("Leg Press", "legs", "machine", True),
    ("Leg Curl", "legs", "machine", False),
    ("Standing Calf Raise", "legs", "machine", False),
    ("Incline Barbell Bench Press", "chest", "barbell", True),
    ("Barbell Bench Press", "chest", "barbell", True),
    ("Weighted Dip", "chest", "bodyweight", True),
    ("Dumbbell Fly", "chest", "dumbbell", False),
    ("Push-Up", "chest", "bodyweight", True),
    ("Pull-Up", "back", "bodyweight", True),
    ("Barbell Row", "back", "barbell", True),
    ("Lat Pulldown", "back", "cable", True),
    ("Deadlift", "back", "barbell", True),
    ("Overhead Press", "shoulders", "barbell", True),
    ("Cable Lateral Raise", "shoulders", "cable", False),
    ("Face Pull", "shoulders", "cable", False),
    ("Barbell Curl", "arms", "barbell", False),
    ("Triceps Pushdown", "arms", "cable", False),
    ("Muscle-Up", "back", "bodyweight", True),
    ("Archer Push-Up", "chest", "bodyweight", True),
    ("Dragon Flag", "core", "bodyweight", False),
    ("Pistol Squat", "legs", "bodyweight", True),
    ("Plank", "core", "bodyweight", False),
    ("Hanging Leg Raise", "core", "bodyweight", False),
]

# (title, description, category, level, minutes, kcal, image, [(exercise, sets, reps, rest)])
ROUTINES = [
    (
        "Upper Body Power & Pump",
        "Heavy incline barbell bench, weighted dips, dumbbell flyes, and cable lateral raises for maximum mass.",
        "Hypertrophy", "Advanced", 60, 520,
        "https://images.unsplash.com/photo-1581009146145-b5ef050c2e1e?w=800&auto=format&fit=crop&q=80",
        [("Incline Barbell Bench Press", 4, 6, 120), ("Weighted Dip", 3, 8, 90), ("Dumbbell Fly", 3, 12, 60),
         ("Cable Lateral Raise", 4, 15, 45), ("Triceps Pushdown", 3, 12, 45)],
    ),
    (
        "Lower Body Quad & Glute Focus",
        "High-bar back squats, Bulgarian split squats, leg curls, and standing calf raise burnouts.",
        "Strength", "Intermediate", 55, 620,
        "https://images.unsplash.com/photo-1517838277536-f5f99be501cd?w=800&auto=format&fit=crop&q=80",
        [("Barbell Back Squat", 4, 6, 150), ("Romanian Deadlift", 3, 8, 120), ("Bulgarian Split Squat", 3, 10, 90),
         ("Leg Curl", 3, 12, 60), ("Standing Calf Raise", 4, 15, 45), ("Plank", 3, 60, 45)],
    ),
    (
        "Bodyweight Explosion & Core",
        "Muscle-ups, archer push-ups, dragon flags, and pistol squats for absolute relative strength.",
        "Calisthenics", "Hardcore", 45, 480,
        "https://images.unsplash.com/photo-1599058945522-28d584b6f0ff?w=800&auto=format&fit=crop&q=80",
        [("Muscle-Up", 4, 5, 120), ("Archer Push-Up", 4, 8, 90), ("Dragon Flag", 3, 6, 90),
         ("Pistol Squat", 3, 6, 90), ("Hanging Leg Raise", 3, 12, 60)],
    ),
]

ACHIEVEMENTS = [
    ("first_workout", "First Blood", "Finish your first workout", "award"),
    ("streak_7", "7-Day Streak", "Consistency", "fire"),
    ("streak_14", "14-Day Streak", "Consistency", "fire"),
    ("bench_100", "100kg Bench", "Power Milestone", "award"),
    ("workouts_100", "Centurion", "100 Workouts", "crown"),
    ("early_bird", "Early Bird", "5AM Session", "sun"),
]


def seed(db: Session) -> None:
    by_name = {e.name: e for e in db.scalars(select(models.Exercise)).all()}
    for name, group, equip, compound in EXERCISES:
        if name not in by_name:
            ex = models.Exercise(name=name, muscle_group=group, equipment=equip, is_compound=compound)
            db.add(ex)
            by_name[name] = ex
    db.flush()

    existing_titles = set(db.scalars(select(models.Routine.title).where(models.Routine.owner_id.is_(None))).all())
    for title, desc, cat, level, mins, kcal, img, items in ROUTINES:
        if title in existing_titles:
            continue
        db.add(
            models.Routine(
                owner_id=None, title=title, description=desc, category=cat, level=level,
                est_minutes=mins, est_kcal=kcal, image_url=img,
                items=[
                    models.RoutineExercise(
                        exercise_id=by_name[n].id, position=pos, target_sets=s, target_reps=r, rest_seconds=rest
                    )
                    for pos, (n, s, r, rest) in enumerate(items)
                ],
            )
        )

    codes = set(db.scalars(select(models.Achievement.code)).all())
    for code, title, sub, icon in ACHIEVEMENTS:
        if code not in codes:
            db.add(models.Achievement(code=code, title=title, subtitle=sub, icon=icon))
    db.commit()


if __name__ == "__main__":
    from .database import SessionLocal

    with SessionLocal() as session:
        seed(session)
    print("Seeded exercises, routines and achievements.")
