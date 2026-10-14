from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import case, delete, distinct, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..deps import current_user
from ..ratelimit import invite_code_limiter

router = APIRouter(tags=["circles"])


# ---------------------------------------------------------------- helpers
def _membership(db: Session, circle_id: int, user_id: int) -> models.CircleMember | None:
    return db.scalar(
        select(models.CircleMember).where(models.CircleMember.circle_id == circle_id, models.CircleMember.user_id == user_id)
    )


def _is_member(db: Session, circle_id: int, user_id: int) -> bool:
    return _membership(db, circle_id, user_id) is not None


def _member_count(db: Session, circle_id: int) -> int:
    return db.scalar(select(func.count()).select_from(models.CircleMember).where(models.CircleMember.circle_id == circle_id)) or 0


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _circle_out(db: Session, c: models.Circle, user: models.User) -> schemas.CircleOut:
    me = _membership(db, c.id, user.id)
    preview = db.scalars(
        select(models.Profile.display_name)
        .join(models.CircleMember, models.CircleMember.user_id == models.Profile.user_id)
        .where(models.CircleMember.circle_id == c.id)
        .order_by(models.CircleMember.joined_at, models.CircleMember.id)
        .limit(4)
    ).all()
    last_post = db.scalar(select(func.max(models.Post.created_at)).where(models.Post.circle_id == c.id))
    last_join = db.scalar(select(func.max(models.CircleMember.joined_at)).where(models.CircleMember.circle_id == c.id))
    last = max((d for d in (_as_utc(last_post), _as_utc(last_join)) if d is not None), default=None)
    return schemas.CircleOut(
        id=c.id,
        name=c.name,
        description=c.description,
        is_public=c.is_public,
        member_count=_member_count(db, c.id),
        is_member=me is not None,
        is_owner=me is not None and me.role == models.ROLE_OWNER,
        role=me.role if me else None,
        invite_code=c.invite_code if me else None,  # never leak the code to non-members
        members_preview=list(preview),
        last_activity_at=last,
    )


def _visible_circle(db: Session, user: models.User, circle_id: int) -> models.Circle:
    """Private circles don't exist as far as non-members are concerned."""
    c = db.get(models.Circle, circle_id)
    if c is None or (not c.is_public and not _is_member(db, c.id, user.id)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Circle not found")
    return c


def _member_circle(db: Session, user: models.User, circle_id: int) -> models.Circle:
    c = _visible_circle(db, user, circle_id)
    if not _is_member(db, c.id, user.id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Join this circle first")
    return c


def _owner_circle(db: Session, user: models.User, circle_id: int) -> models.Circle:
    c = _member_circle(db, user, circle_id)
    me = _membership(db, c.id, user.id)
    if me is None or me.role != models.ROLE_OWNER:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the circle owner can do that")
    return c


def _check_invite_rate(user: models.User) -> None:
    if not invite_code_limiter.allow(user.id):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many invite attempts. Try again in a few minutes.")


INVALID_CODE = "This invite link is invalid or has expired."


def _circle_by_code(db: Session, code: str) -> models.Circle:
    c = db.scalar(select(models.Circle).where(models.Circle.invite_code == code.strip()))
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, INVALID_CODE)
    return c


def _add_member(db: Session, circle: models.Circle, user_id: int) -> None:
    """Idempotent: adding someone who is already a member is a no-op."""
    if _is_member(db, circle.id, user_id):
        return
    db.add(models.CircleMember(circle_id=circle.id, user_id=user_id, role=models.ROLE_MEMBER))
    try:
        db.commit()
    except IntegrityError:  # a concurrent request added them first
        db.rollback()


def _posts_out(db: Session, posts: list[models.Post], user: models.User) -> list[schemas.PostOut]:
    if not posts:
        return []
    ids = [p.id for p in posts]
    likes = dict(
        db.execute(select(models.PostLike.post_id, func.count()).where(models.PostLike.post_id.in_(ids)).group_by(models.PostLike.post_id)).all()
    )
    mine = set(db.scalars(select(models.PostLike.post_id).where(models.PostLike.post_id.in_(ids), models.PostLike.user_id == user.id)).all())
    comments = dict(
        db.execute(select(models.Comment.post_id, func.count()).where(models.Comment.post_id.in_(ids)).group_by(models.Comment.post_id)).all()
    )
    names = dict(
        db.execute(select(models.Profile.user_id, models.Profile.display_name).where(models.Profile.user_id.in_({p.user_id for p in posts}))).all()
    )
    circles = dict(
        db.execute(select(models.Circle.id, models.Circle.name).where(models.Circle.id.in_({p.circle_id for p in posts}))).all()
    )

    out = []
    for p in posts:
        workout = None
        if p.session_id is not None:
            s = db.get(models.WorkoutSession, p.session_id)
            if s is not None:
                workout = schemas.WorkoutSummary(
                    title=s.title,
                    sets=len(s.sets),
                    volume_kg=round(sum(x.weight_kg * x.reps for x in s.sets), 1),
                    pr_count=sum(1 for x in s.sets if x.is_pr),
                )
        out.append(
            schemas.PostOut(
                id=p.id,
                circle_id=p.circle_id,
                circle_name=circles.get(p.circle_id, ""),
                author_id=p.user_id,
                author_name=names.get(p.user_id, "Athlete"),
                is_mine=p.user_id == user.id,
                body=p.body,
                created_at=p.created_at,
                like_count=likes.get(p.id, 0),
                liked_by_me=p.id in mine,
                comment_count=comments.get(p.id, 0),
                workout=workout,
            )
        )
    return out


# ---------------------------------------------------------------- circles
@router.get("/circles", response_model=list[schemas.CircleOut])
def list_circles(
    mine: bool = False,
    q: str | None = Query(None, max_length=60),
    user: models.User = Depends(current_user),
    db: Session = Depends(get_db),
):
    my_ids = select(models.CircleMember.circle_id).where(models.CircleMember.user_id == user.id)
    stmt = select(models.Circle).order_by(models.Circle.name)
    if mine:
        stmt = stmt.where(models.Circle.id.in_(my_ids))
    else:
        stmt = stmt.where((models.Circle.is_public.is_(True)) | (models.Circle.id.in_(my_ids)))
    if q:
        stmt = stmt.where(models.Circle.name.ilike(f"%{q}%"))
    return [_circle_out(db, c, user) for c in db.scalars(stmt).all()]


@router.post("/circles", response_model=schemas.CircleOut, status_code=status.HTTP_201_CREATED)
def create_circle(body: schemas.CircleCreate, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    circle = models.Circle(name=body.name.strip(), description=body.description.strip(), is_public=body.is_public, created_by=user.id)
    db.add(circle)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "A circle with this name already exists")
    db.add(models.CircleMember(circle_id=circle.id, user_id=user.id, role=models.ROLE_OWNER))
    db.commit()
    return _circle_out(db, circle, user)


# Declared before /circles/{circle_id} so "join-preview" is never parsed as an id.
@router.get("/circles/join-preview", response_model=schemas.JoinPreviewOut)
def join_preview(
    code: str = Query(min_length=4, max_length=32),
    user: models.User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """What an invite link points at. Every invalid code gets the same 404."""
    _check_invite_rate(user)
    c = _circle_by_code(db, code)
    return schemas.JoinPreviewOut(
        id=c.id,
        name=c.name,
        description=c.description,
        member_count=_member_count(db, c.id),
        is_public=c.is_public,
        is_member=_is_member(db, c.id, user.id),
    )


@router.post("/circles/join-by-code", response_model=schemas.CircleOut)
def join_by_code(body: schemas.JoinByCodeIn, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    _check_invite_rate(user)
    c = _circle_by_code(db, body.code)
    _add_member(db, c, user.id)
    return _circle_out(db, c, user)


@router.get("/circles/{circle_id}", response_model=schemas.CircleOut)
def get_circle(circle_id: int, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    return _circle_out(db, _visible_circle(db, user, circle_id), user)


@router.patch("/circles/{circle_id}", response_model=schemas.CircleOut)
def update_circle(circle_id: int, body: schemas.CircleUpdate, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    circle = _owner_circle(db, user, circle_id)
    data = body.model_dump(exclude_unset=True)
    if data.get("name") is not None:
        circle.name = data["name"].strip()
    if data.get("description") is not None:
        circle.description = data["description"].strip()
    if data.get("is_public") is not None:
        circle.is_public = data["is_public"]
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "A circle with this name already exists")
    return _circle_out(db, circle, user)


def _delete_circle_rows(db: Session, circle_id: int) -> None:
    """Explicit deletes: SQLite doesn't enforce ON DELETE CASCADE unless foreign keys are switched on."""
    post_ids = select(models.Post.id).where(models.Post.circle_id == circle_id)
    db.execute(delete(models.PostLike).where(models.PostLike.post_id.in_(post_ids)))
    db.execute(delete(models.Comment).where(models.Comment.post_id.in_(post_ids)))
    db.execute(delete(models.Post).where(models.Post.circle_id == circle_id))
    db.execute(delete(models.CircleMember).where(models.CircleMember.circle_id == circle_id))
    db.execute(delete(models.CircleChallenge).where(models.CircleChallenge.circle_id == circle_id))


@router.delete("/circles/{circle_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_circle(circle_id: int, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    """Owner only. Removes the circle and everything under it."""
    circle = _owner_circle(db, user, circle_id)
    _delete_circle_rows(db, circle.id)
    db.delete(circle)
    db.commit()


@router.post("/circles/{circle_id}/invite-code/reset", response_model=schemas.CircleOut)
def reset_invite_code(circle_id: int, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    circle = _owner_circle(db, user, circle_id)
    circle.invite_code = models.new_invite_code()
    db.commit()
    return _circle_out(db, circle, user)


@router.post("/circles/{circle_id}/join", response_model=schemas.CircleOut)
def join_circle(circle_id: int, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    circle = _visible_circle(db, user, circle_id)
    if not _is_member(db, circle.id, user.id):
        if not circle.is_public:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "This circle is invite-only")
        _add_member(db, circle, user.id)
    return _circle_out(db, circle, user)


@router.post("/circles/{circle_id}/leave", status_code=status.HTTP_204_NO_CONTENT)
def leave_circle(circle_id: int, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    member = _membership(db, circle_id, user.id)
    if member is None:
        return
    if member.role == models.ROLE_OWNER:
        raise HTTPException(status.HTTP_409_CONFLICT, "Transfer ownership to another member or delete the circle first")
    db.delete(member)
    db.commit()


# --------------------------------------------------------------- members
@router.get("/circles/{circle_id}/members", response_model=list[schemas.MemberOut])
def list_members(circle_id: int, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    _member_circle(db, user, circle_id)
    rows = db.execute(
        select(models.CircleMember, models.Profile.display_name)
        .join(models.Profile, models.Profile.user_id == models.CircleMember.user_id)
        .where(models.CircleMember.circle_id == circle_id)
        .order_by(case((models.CircleMember.role == models.ROLE_OWNER, 0), else_=1), models.CircleMember.joined_at, models.CircleMember.id)
    ).all()
    return [
        schemas.MemberOut(user_id=m.user_id, display_name=name, role=m.role, joined_at=m.joined_at, is_me=m.user_id == user.id)
        for m, name in rows
    ]


ADD_BY_EMAIL_REPLY = "If an account exists for that email, it is now a member of this circle."


@router.post("/circles/{circle_id}/members", response_model=schemas.MessageOut, status_code=status.HTTP_202_ACCEPTED)
def add_member_by_email(circle_id: int, body: schemas.MemberAddIn, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    """Owner adds an existing user. The reply is identical whether or not the email has an account."""
    circle = _owner_circle(db, user, circle_id)
    target = db.scalar(select(models.User).where(func.lower(models.User.email) == body.email.lower()))
    if target is not None:
        _add_member(db, circle, target.id)
    return schemas.MessageOut(detail=ADD_BY_EMAIL_REPLY)


@router.delete("/circles/{circle_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_member(circle_id: int, user_id: int, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    circle = _owner_circle(db, user, circle_id)
    if user_id == user.id:
        raise HTTPException(status.HTTP_409_CONFLICT, "Transfer ownership or delete the circle instead")
    member = _membership(db, circle.id, user_id)
    if member is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not a member of this circle")
    db.delete(member)
    db.commit()


@router.post("/circles/{circle_id}/transfer", response_model=schemas.CircleOut)
def transfer_ownership(circle_id: int, body: schemas.TransferIn, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    """Owner hands the circle to another member and becomes a regular member."""
    circle = _owner_circle(db, user, circle_id)
    target = _membership(db, circle.id, body.user_id)
    if target is None or body.user_id == user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Pick another member of this circle")
    me = _membership(db, circle.id, user.id)
    assert me is not None
    me.role = models.ROLE_MEMBER
    target.role = models.ROLE_OWNER
    db.commit()
    return _circle_out(db, circle, user)


# ------------------------------------------------------------------ feed
@router.get("/circles/{circle_id}/feed", response_model=list[schemas.PostOut])
def feed(
    circle_id: int,
    limit: int = Query(20, ge=1, le=50),
    before_id: int | None = None,
    user: models.User = Depends(current_user),
    db: Session = Depends(get_db),
):
    _member_circle(db, user, circle_id)
    stmt = select(models.Post).where(models.Post.circle_id == circle_id).order_by(models.Post.id.desc()).limit(limit)
    if before_id is not None:
        stmt = stmt.where(models.Post.id < before_id)
    return _posts_out(db, list(db.scalars(stmt).all()), user)


@router.post("/circles/{circle_id}/posts", response_model=schemas.PostOut, status_code=status.HTTP_201_CREATED)
def create_post(circle_id: int, body: schemas.PostCreate, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    _member_circle(db, user, circle_id)
    if body.session_id is not None:
        s = db.get(models.WorkoutSession, body.session_id)
        if s is None or s.user_id != user.id or s.finished_at is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Pick one of your finished workouts")
    post = models.Post(circle_id=circle_id, user_id=user.id, body=body.body.strip(), session_id=body.session_id)
    db.add(post)
    db.commit()
    db.refresh(post)
    return _posts_out(db, [post], user)[0]


def _post_for_member(db: Session, user: models.User, post_id: int) -> models.Post:
    post = db.get(models.Post, post_id)
    if post is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Post not found")
    _member_circle(db, user, post.circle_id)
    return post


@router.delete("/posts/{post_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_post(post_id: int, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    post = db.get(models.Post, post_id)
    if post is None or post.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Post not found")
    db.delete(post)
    db.commit()


@router.post("/posts/{post_id}/like", response_model=schemas.LikeOut)
def toggle_like(post_id: int, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    post = _post_for_member(db, user, post_id)
    like = db.scalar(select(models.PostLike).where(models.PostLike.post_id == post.id, models.PostLike.user_id == user.id))
    if like is None:
        db.add(models.PostLike(post_id=post.id, user_id=user.id))
        liked = True
    else:
        db.delete(like)
        liked = False
    db.commit()
    count = db.scalar(select(func.count()).select_from(models.PostLike).where(models.PostLike.post_id == post.id))
    return schemas.LikeOut(liked=liked, like_count=count or 0)


@router.get("/posts/{post_id}/comments", response_model=list[schemas.CommentOut])
def list_comments(post_id: int, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    post = _post_for_member(db, user, post_id)
    rows = db.execute(
        select(models.Comment, models.Profile.display_name)
        .join(models.Profile, models.Profile.user_id == models.Comment.user_id)
        .where(models.Comment.post_id == post.id)
        .order_by(models.Comment.id)
    ).all()
    return [
        schemas.CommentOut(id=c.id, author_name=name, is_mine=c.user_id == user.id, body=c.body, created_at=c.created_at)
        for c, name in rows
    ]


@router.post("/posts/{post_id}/comments", response_model=schemas.CommentOut, status_code=status.HTTP_201_CREATED)
def add_comment(post_id: int, body: schemas.CommentCreate, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    post = _post_for_member(db, user, post_id)
    c = models.Comment(post_id=post.id, user_id=user.id, body=body.body.strip())
    db.add(c)
    db.commit()
    db.refresh(c)
    return schemas.CommentOut(id=c.id, author_name=user.profile.display_name, is_mine=True, body=c.body, created_at=c.created_at)


# ----------------------------------------------------------- leaderboard
@router.get("/circles/{circle_id}/leaderboard", response_model=list[schemas.LeaderRow])
def leaderboard(
    circle_id: int,
    days: int = Query(7, ge=1, le=90),
    user: models.User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Members ranked by training volume over the last `days` days (finished workouts only)."""
    _member_circle(db, user, circle_id)
    since = datetime.now(timezone.utc) - timedelta(days=days)

    members = db.execute(
        select(models.CircleMember.user_id, models.Profile.display_name)
        .join(models.Profile, models.Profile.user_id == models.CircleMember.user_id)
        .where(models.CircleMember.circle_id == circle_id)
    ).all()
    stats = {
        uid: (workouts, volume or 0.0, prs or 0)
        for uid, workouts, volume, prs in db.execute(
            select(
                models.WorkoutSession.user_id,
                func.count(distinct(models.WorkoutSession.id)),
                func.sum(models.SessionSet.weight_kg * models.SessionSet.reps),
                func.sum(case((models.SessionSet.is_pr.is_(True), 1), else_=0)),
            )
            .join(models.SessionSet, models.SessionSet.session_id == models.WorkoutSession.id)
            .where(
                models.WorkoutSession.user_id.in_([m[0] for m in members]),
                models.WorkoutSession.finished_at.is_not(None),
                models.WorkoutSession.finished_at >= since,
            )
            .group_by(models.WorkoutSession.user_id)
        ).all()
    }
    rows = []
    for uid, name in members:
        w, v, p = stats.get(uid, (0, 0.0, 0))
        rows.append((uid, name, w, round(v, 1), p))
    rows.sort(key=lambda r: (-r[3], -r[2], r[1].lower()))
    return [
        schemas.LeaderRow(rank=i + 1, user_id=uid, display_name=name, workouts=w, volume_kg=v, prs=p, is_me=uid == user.id)
        for i, (uid, name, w, v, p) in enumerate(rows)
    ]
