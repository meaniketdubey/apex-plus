"""Database schema.

Column types are kept portable so the same models work on PostgreSQL (production)
and SQLite (local dev / tests).

Derived values (streak, muscle load, PRs, calories) are NOT stored here. They are
computed from `session_sets` in the dashboard service.
"""
import secrets
from datetime import date, datetime, timezone

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_invite_code() -> str:
    """Unguessable, URL-safe circle invite token (~11 chars, 64 bits of entropy)."""
    return secrets.token_urlsafe(8)


# ---------------------------------------------------------------- users
class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    profile: Mapped["Profile"] = relationship(back_populates="user", uselist=False, cascade="all, delete-orphan")


class Profile(Base):
    __tablename__ = "profiles"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(80), default="Athlete")
    height_cm: Mapped[float | None] = mapped_column(Float, nullable=True)
    weight_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    goal: Mapped[str] = mapped_column(String(40), default="Muscle Building")
    discipline: Mapped[str] = mapped_column(String(40), default="Hypertrophy & Power")
    equipment: Mapped[str] = mapped_column(String(255), default="")  # comma separated, e.g. "barbell,dumbbell"
    days_per_week: Mapped[int] = mapped_column(Integer, default=4)
    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    onboarded: Mapped[bool] = mapped_column(Boolean, default=False)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")  # IANA name, e.g. Asia/Kolkata
    target_weight_kg: Mapped[float | None] = mapped_column(Float, nullable=True)  # body-weight goal

    user: Mapped[User] = relationship(back_populates="profile")


class BodyMetric(Base):
    __tablename__ = "body_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    logged_on: Mapped[date] = mapped_column(Date, default=date.today, index=True)
    weight_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    body_fat_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    chest_cm: Mapped[float | None] = mapped_column(Float, nullable=True)
    arms_cm: Mapped[float | None] = mapped_column(Float, nullable=True)
    waist_cm: Mapped[float | None] = mapped_column(Float, nullable=True)
    thighs_cm: Mapped[float | None] = mapped_column(Float, nullable=True)


class WaterLog(Base):
    __tablename__ = "water_logs"
    __table_args__ = (UniqueConstraint("user_id", "logged_on"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    logged_on: Mapped[date] = mapped_column(Date, default=date.today)
    litres: Mapped[float] = mapped_column(Float, default=0.0)


# ------------------------------------------------------------- training
class Exercise(Base):
    __tablename__ = "exercises"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    muscle_group: Mapped[str] = mapped_column(String(40), index=True)  # legs, chest, back, shoulders, arms, core
    equipment: Mapped[str] = mapped_column(String(40), default="bodyweight")
    is_compound: Mapped[bool] = mapped_column(Boolean, default=False)


class Routine(Base):
    __tablename__ = "routines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)  # null = built-in
    title: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(40), default="Hypertrophy")
    level: Mapped[str] = mapped_column(String(20), default="Intermediate")
    est_minutes: Mapped[int] = mapped_column(Integer, default=45)
    est_kcal: Mapped[int] = mapped_column(Integer, default=400)
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    items: Mapped[list["RoutineExercise"]] = relationship(
        back_populates="routine", cascade="all, delete-orphan", order_by="RoutineExercise.position"
    )


class RoutineExercise(Base):
    __tablename__ = "routine_exercises"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    routine_id: Mapped[int] = mapped_column(ForeignKey("routines.id", ondelete="CASCADE"), index=True)
    exercise_id: Mapped[int] = mapped_column(ForeignKey("exercises.id"))
    position: Mapped[int] = mapped_column(Integer, default=0)
    target_sets: Mapped[int] = mapped_column(Integer, default=3)
    target_reps: Mapped[int] = mapped_column(Integer, default=10)
    rest_seconds: Mapped[int] = mapped_column(Integer, default=60)

    routine: Mapped[Routine] = relationship(back_populates="items")
    exercise: Mapped[Exercise] = relationship()


class WorkoutSession(Base):
    __tablename__ = "workout_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    routine_id: Mapped[int | None] = mapped_column(ForeignKey("routines.id", ondelete="SET NULL"), nullable=True)
    title: Mapped[str] = mapped_column(String(120), default="Workout")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")

    sets: Mapped[list["SessionSet"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", order_by="SessionSet.id"
    )


class SessionSet(Base):
    """One logged set. This is the core table: PRs, volume, muscle load all derive from it."""

    __tablename__ = "session_sets"
    # client_id is generated by the app per set so a retry after a dropped connection cannot create a duplicate.
    __table_args__ = (UniqueConstraint("session_id", "client_id", name="uq_session_sets_session_client"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("workout_sessions.id", ondelete="CASCADE"), index=True)
    exercise_id: Mapped[int] = mapped_column(ForeignKey("exercises.id"), index=True)
    set_number: Mapped[int] = mapped_column(Integer, default=1)
    weight_kg: Mapped[float] = mapped_column(Float, default=0.0)
    reps: Mapped[int] = mapped_column(Integer, default=0)
    rpe: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_pr: Mapped[bool] = mapped_column(Boolean, default=False)
    client_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    logged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    session: Mapped[WorkoutSession] = relationship(back_populates="sets")
    exercise: Mapped[Exercise] = relationship()


# ------------------------------------------------------------ community
class Circle(Base):
    __tablename__ = "circles"
    __table_args__ = (UniqueConstraint("invite_code", name="uq_circles_invite_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    is_public: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    # Anyone holding this code can join, public or private. Rotating it kills old links.
    invite_code: Mapped[str] = mapped_column(String(32), default=new_invite_code)


ROLE_OWNER = "owner"
ROLE_MEMBER = "member"


class CircleMember(Base):
    __tablename__ = "circle_members"
    __table_args__ = (UniqueConstraint("circle_id", "user_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    circle_id: Mapped[int] = mapped_column(ForeignKey("circles.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    role: Mapped[str] = mapped_column(String(10), default=ROLE_MEMBER, server_default=ROLE_MEMBER)  # owner | member


CHALLENGE_KINDS = ("volume", "workouts", "streak")


class CircleChallenge(Base):
    """A dated competition inside a circle. Results are computed from session_sets, never stored."""

    __tablename__ = "circle_challenges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    circle_id: Mapped[int] = mapped_column(ForeignKey("circles.id", ondelete="CASCADE"), index=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    title: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(16))  # volume | workouts | streak
    target: Mapped[float | None] = mapped_column(Float, nullable=True)  # optional goal, drives the progress bar
    starts_on: Mapped[date] = mapped_column(Date)
    ends_on: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Post(Base):
    __tablename__ = "posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    circle_id: Mapped[int] = mapped_column(ForeignKey("circles.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    body: Mapped[str] = mapped_column(Text)
    session_id: Mapped[int | None] = mapped_column(ForeignKey("workout_sessions.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class PostLike(Base):
    __tablename__ = "post_likes"
    __table_args__ = (UniqueConstraint("post_id", "user_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))


class Comment(Base):
    __tablename__ = "comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# --------------------------------------------------------- achievements
class Achievement(Base):
    __tablename__ = "achievements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    title: Mapped[str] = mapped_column(String(80))
    subtitle: Mapped[str] = mapped_column(String(120), default="")
    icon: Mapped[str] = mapped_column(String(40), default="award")


class UserAchievement(Base):
    __tablename__ = "user_achievements"
    __table_args__ = (UniqueConstraint("user_id", "achievement_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    achievement_id: Mapped[int] = mapped_column(ForeignKey("achievements.id"))
    unlocked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ------------------------------------------------------------------- AI
class AIConversation(Base):
    """Placeholder for the AI coach: stores chat turns so they can be replayed / audited."""

    __tablename__ = "ai_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(12))  # user | assistant | tool
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
