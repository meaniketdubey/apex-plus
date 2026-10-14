from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..deps import current_user
from ..timeutil import local_today

router = APIRouter(tags=["metrics"])


@router.get("/metrics", response_model=list[schemas.MetricOut])
def list_metrics(
    limit: int = Query(30, ge=1, le=365),
    user: models.User = Depends(current_user),
    db: Session = Depends(get_db),
):
    q = (
        select(models.BodyMetric)
        .where(models.BodyMetric.user_id == user.id)
        .order_by(models.BodyMetric.logged_on.desc(), models.BodyMetric.id.desc())
        .limit(limit)
    )
    return db.scalars(q).all()


@router.post("/metrics", response_model=schemas.MetricOut, status_code=status.HTTP_201_CREATED)
def add_metric(
    body: schemas.MetricIn,
    user: models.User = Depends(current_user),
    db: Session = Depends(get_db),
):
    data = body.model_dump()
    if all(v is None for k, v in data.items() if k != "logged_on"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Provide at least one measurement")
    data["logged_on"] = data["logged_on"] or local_today(user)
    metric = models.BodyMetric(user_id=user.id, **data)
    db.add(metric)
    # Keep the profile's headline weight in sync with the newest weigh-in.
    if metric.weight_kg is not None:
        user.profile.weight_kg = metric.weight_kg
    db.commit()
    db.refresh(metric)
    return metric


MEASUREMENTS = ("weight_kg", "body_fat_pct", "chest_cm", "arms_cm", "waist_cm", "thighs_cm")


@router.patch("/metrics/{metric_id}", response_model=schemas.MetricOut)
def update_metric(
    metric_id: int,
    body: schemas.MetricUpdate,
    user: models.User = Depends(current_user),
    db: Session = Depends(get_db),
):
    metric = db.get(models.BodyMetric, metric_id)
    if metric is None or metric.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Entry not found")
    data = body.model_dump(exclude_unset=True)
    if "logged_on" in data:
        if data["logged_on"] is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "An entry needs a date")
        if data["logged_on"] > local_today(user):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "That date is in the future")
    for field, value in data.items():
        setattr(metric, field, value)
    if all(getattr(metric, f) is None for f in MEASUREMENTS):
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Keep at least one measurement, or delete the entry")
    db.flush()
    # Keep the profile's headline weight in sync with the newest weigh-in.
    newest = db.scalar(
        select(models.BodyMetric)
        .where(models.BodyMetric.user_id == user.id, models.BodyMetric.weight_kg.is_not(None))
        .order_by(models.BodyMetric.logged_on.desc(), models.BodyMetric.id.desc())
        .limit(1)
    )
    if newest is not None:
        user.profile.weight_kg = newest.weight_kg
    db.commit()
    db.refresh(metric)
    return metric


@router.delete("/metrics/{metric_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_metric(
    metric_id: int,
    user: models.User = Depends(current_user),
    db: Session = Depends(get_db),
):
    metric = db.get(models.BodyMetric, metric_id)
    if metric is None or metric.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Entry not found")
    db.delete(metric)
    db.commit()


@router.get("/water", response_model=schemas.WaterOut)
def get_water(user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    today = local_today(user)
    log = db.scalar(
        select(models.WaterLog).where(models.WaterLog.user_id == user.id, models.WaterLog.logged_on == today)
    )
    return schemas.WaterOut(logged_on=today, litres=log.litres if log else 0.0)


@router.post("/water", response_model=schemas.WaterOut)
def add_water(
    body: schemas.WaterIn,
    user: models.User = Depends(current_user),
    db: Session = Depends(get_db),
):
    today = local_today(user)
    log = db.scalar(
        select(models.WaterLog).where(models.WaterLog.user_id == user.id, models.WaterLog.logged_on == today)
    )
    if log is None:
        log = models.WaterLog(user_id=user.id, logged_on=today, litres=0.0)
        db.add(log)
    log.litres = round(log.litres + body.add_litres, 2)
    db.commit()
    return schemas.WaterOut(logged_on=today, litres=log.litres)
