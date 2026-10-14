from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ----------------------------------------------------------------- auth
class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(default="Athlete", min_length=1, max_length=80)
    timezone: str | None = Field(default=None, max_length=64)


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class RefreshIn(BaseModel):
    refresh_token: str


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


# -------------------------------------------------------------- profile
class ProfileOut(ORM):
    display_name: str
    height_cm: float | None
    weight_kg: float | None
    goal: str
    discipline: str
    equipment: str
    days_per_week: int
    avatar_url: str | None
    onboarded: bool
    timezone: str
    target_weight_kg: float | None = None


class MeOut(BaseModel):
    id: int
    email: str
    profile: ProfileOut


class ProfileUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=80)
    height_cm: float | None = Field(default=None, ge=100, le=250)
    weight_kg: float | None = Field(default=None, ge=25, le=400)
    goal: str | None = Field(default=None, max_length=40)
    discipline: str | None = Field(default=None, max_length=40)
    equipment: str | None = Field(default=None, max_length=255)
    days_per_week: int | None = Field(default=None, ge=1, le=7)
    onboarded: bool | None = None
    timezone: str | None = Field(default=None, max_length=64)
    target_weight_kg: float | None = Field(default=None, ge=25, le=400)  # send null to clear the goal


# -------------------------------------------------------------- metrics
class MetricIn(BaseModel):
    logged_on: date | None = None
    weight_kg: float | None = Field(default=None, ge=25, le=400)
    body_fat_pct: float | None = Field(default=None, ge=2, le=70)
    chest_cm: float | None = Field(default=None, ge=30, le=250)
    arms_cm: float | None = Field(default=None, ge=10, le=100)
    waist_cm: float | None = Field(default=None, ge=30, le=250)
    thighs_cm: float | None = Field(default=None, ge=20, le=150)


class MetricUpdate(BaseModel):
    """Partial edit of one entry. Send null to clear a measurement; at least one must remain."""

    logged_on: date | None = None
    weight_kg: float | None = Field(default=None, ge=25, le=400)
    body_fat_pct: float | None = Field(default=None, ge=2, le=70)
    chest_cm: float | None = Field(default=None, ge=30, le=250)
    arms_cm: float | None = Field(default=None, ge=10, le=100)
    waist_cm: float | None = Field(default=None, ge=30, le=250)
    thighs_cm: float | None = Field(default=None, ge=20, le=150)


class MetricOut(ORM):
    id: int
    logged_on: date
    weight_kg: float | None
    body_fat_pct: float | None
    chest_cm: float | None
    arms_cm: float | None
    waist_cm: float | None
    thighs_cm: float | None


class WaterIn(BaseModel):
    add_litres: float = Field(gt=0, le=5)


class WaterOut(BaseModel):
    logged_on: date
    litres: float


# ------------------------------------------------------------- training
class ExerciseOut(ORM):
    id: int
    name: str
    muscle_group: str
    equipment: str
    is_compound: bool


class RoutineItemIn(BaseModel):
    exercise_id: int
    target_sets: int = Field(default=3, ge=1, le=20)
    target_reps: int = Field(default=10, ge=1, le=100)
    rest_seconds: int = Field(default=60, ge=0, le=600)


class RoutineItemOut(ORM):
    id: int
    position: int
    target_sets: int
    target_reps: int
    rest_seconds: int
    exercise: ExerciseOut


class RoutineCreate(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    category: str = Field(default="Hypertrophy", min_length=1, max_length=40)
    level: str = Field(default="Intermediate", min_length=1, max_length=20)
    est_minutes: int = Field(default=45, ge=5, le=300)
    items: list[RoutineItemIn] = Field(min_length=1, max_length=30)


class RoutineOut(ORM):
    id: int
    owner_id: int | None
    title: str
    description: str
    category: str
    level: str
    est_minutes: int
    est_kcal: int
    image_url: str | None
    items: list[RoutineItemOut]
    last_done_at: datetime | None = None  # your most recent finished session of this routine


class SessionStart(BaseModel):
    routine_id: int | None = None
    title: str | None = Field(default=None, max_length=120)


class SetIn(BaseModel):
    exercise_id: int
    set_number: int = Field(default=1, ge=1, le=50)
    weight_kg: float = Field(default=0, ge=0, le=1000)
    reps: int = Field(default=0, ge=0, le=500)
    rpe: float | None = Field(default=None, ge=1, le=10)
    client_id: str | None = Field(default=None, max_length=64)


class SetOut(ORM):
    id: int
    exercise_id: int
    set_number: int
    weight_kg: float
    reps: int
    rpe: float | None
    is_pr: bool
    client_id: str | None = None
    logged_at: datetime


class SessionFinish(BaseModel):
    notes: str = Field(default="", max_length=2000)


class SessionOut(ORM):
    id: int
    routine_id: int | None
    title: str
    started_at: datetime
    finished_at: datetime | None
    notes: str
    sets: list[SetOut]


class ExerciseHistoryOut(BaseModel):
    exercise_id: int
    session_id: int | None
    sets: list[SetOut]
    best_est_1rm: float | None


# ------------------------------------------------------------ dashboard
class MuscleLoad(BaseModel):
    muscle_group: str
    volume_kg: float
    percent: int  # relative to the most-trained group this week
    label: str  # Rested / Recovered / Optimal / High


class WeightPoint(BaseModel):
    date: date
    weight_kg: float | None
    body_fat_pct: float | None


class DashboardOut(BaseModel):
    streak_days: int
    workouts_this_week: int
    total_workouts: int
    minutes_today: int
    water_litres_today: float
    muscle_load: list[MuscleLoad]
    latest_metric: MetricOut | None
    weight_history: list[WeightPoint]


# -------------------------------------------------------------- circles
class CircleCreate(BaseModel):
    name: str = Field(min_length=3, max_length=80)
    description: str = Field(default="", max_length=500)
    is_public: bool = True


class CircleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=3, max_length=80)
    description: str | None = Field(default=None, max_length=500)
    is_public: bool | None = None


class CircleOut(BaseModel):
    id: int
    name: str
    description: str
    is_public: bool
    member_count: int
    is_member: bool
    is_owner: bool
    role: str | None = None  # "owner" | "member" for members, else None
    invite_code: str | None = None  # only ever filled in for members
    members_preview: list[str] = []  # up to 4 display names, for avatar initials
    last_activity_at: datetime | None = None  # newest post or join


class JoinPreviewOut(BaseModel):
    id: int
    name: str
    description: str
    member_count: int
    is_public: bool
    is_member: bool


class JoinByCodeIn(BaseModel):
    code: str = Field(min_length=4, max_length=32)


class MemberAddIn(BaseModel):
    email: EmailStr


class MemberOut(BaseModel):
    user_id: int
    display_name: str
    role: str
    joined_at: datetime
    is_me: bool


class TransferIn(BaseModel):
    user_id: int


class MessageOut(BaseModel):
    detail: str


class PostCreate(BaseModel):
    body: str = Field(min_length=1, max_length=1000)
    session_id: int | None = None  # attach one of your finished workouts


class WorkoutSummary(BaseModel):
    title: str
    sets: int
    volume_kg: float
    pr_count: int


class PostOut(BaseModel):
    id: int
    circle_id: int
    circle_name: str
    author_id: int
    author_name: str
    is_mine: bool
    body: str
    created_at: datetime
    like_count: int
    liked_by_me: bool
    comment_count: int
    workout: WorkoutSummary | None


class LikeOut(BaseModel):
    liked: bool
    like_count: int


class CommentCreate(BaseModel):
    body: str = Field(min_length=1, max_length=500)


class CommentOut(BaseModel):
    id: int
    author_name: str
    is_mine: bool
    body: str
    created_at: datetime


class LeaderRow(BaseModel):
    rank: int
    user_id: int
    display_name: str
    workouts: int
    volume_kg: float
    prs: int
    is_me: bool


# ------------------------------------------------------------------- AI
class AIStatusOut(BaseModel):
    llm_enabled: bool  # coach chat needs a key; the other AI features work without one


class ParseWorkoutIn(BaseModel):
    text: str = Field(min_length=1, max_length=1000)


class ParsedSetOut(BaseModel):
    weight_kg: float
    reps: int


class ParsedItemOut(BaseModel):
    text: str  # what the user wrote
    exercise_id: int | None
    exercise_name: str | None
    sets: list[ParsedSetOut]


class ParseWorkoutOut(BaseModel):
    items: list[ParsedItemOut]
    unmatched: list[str]
    source: str  # "llm" | "rules"


class NextWeightOut(BaseModel):
    exercise_id: int
    exercise_name: str
    target_reps: int
    suggested_weight_kg: float | None
    last_weight_kg: float | None
    reason: str


class InsightOut(BaseModel):
    headline: str
    bullets: list[str]
    stats: dict


class GenerateRoutineIn(BaseModel):
    goal: str | None = Field(default=None, max_length=40)
    minutes: int = Field(default=45, ge=15, le=120)
    focus: str = Field(default="full body", max_length=20)
    equipment: list[str] | None = None


class GeneratedItemOut(BaseModel):
    exercise_id: int
    exercise_name: str
    muscle_group: str
    target_sets: int
    target_reps: int
    rest_seconds: int


class GeneratedRoutineOut(BaseModel):
    title: str
    description: str
    category: str
    level: str
    est_minutes: int
    items: list[GeneratedItemOut]
    source: str


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=1000)


class ChatOut(BaseModel):
    reply: str


class ChatMessageOut(ORM):
    id: int
    role: str
    content: str
    created_at: datetime


# ----------------------------------------------------------- challenges
class ChallengeCreate(BaseModel):
    title: str = Field(min_length=3, max_length=80)
    kind: Literal["volume", "workouts", "streak"]
    target: float | None = Field(default=None, gt=0, le=10_000_000)
    starts_on: date
    ends_on: date

    @model_validator(mode="after")
    def _dates(self):
        if self.ends_on < self.starts_on:
            raise ValueError("ends_on must be on or after starts_on")
        if (self.ends_on - self.starts_on).days > 365:
            raise ValueError("A challenge can last at most a year")
        return self


class ChallengeRow(BaseModel):
    rank: int
    user_id: int
    display_name: str
    value: float
    progress: float  # 0..1 toward the target, or relative to the leader when there is no target
    is_me: bool


class ChallengeOut(BaseModel):
    id: int
    circle_id: int
    title: str
    kind: str
    target: float | None
    starts_on: date
    ends_on: date
    status: str  # upcoming | active | ended
    days_left: int
    my_value: float
    my_rank: int | None
    leader_name: str | None
    leader_value: float


class ChallengeDetailOut(ChallengeOut):
    leaderboard: list[ChallengeRow]


# ------------------------------------------------------------- progress
class LoggedExerciseOut(BaseModel):
    id: int
    name: str
    muscle_group: str
    sessions: int
    last_logged_at: datetime | None


class ExercisePoint(BaseModel):
    date: date
    best_est_1rm: float
    best_weight_kg: float
    best_reps: int
    top_weight_kg: float
    volume_kg: float


class ExerciseProgressOut(BaseModel):
    exercise_id: int
    name: str
    muscle_group: str
    points: list[ExercisePoint]


class RecordOut(BaseModel):
    exercise_id: int
    name: str
    muscle_group: str
    est_1rm: float
    weight_kg: float
    reps: int
    achieved_on: date
    is_recent: bool


class PeriodTotals(BaseModel):
    workouts: int
    volume_kg: float
    active_minutes: int


class PeriodChange(BaseModel):
    workouts_pct: float | None  # None when the previous period was zero
    volume_pct: float | None
    minutes_pct: float | None


class SummaryOut(BaseModel):
    period: str
    days: int
    start: date
    end: date
    current: PeriodTotals
    previous: PeriodTotals
    change: PeriodChange
