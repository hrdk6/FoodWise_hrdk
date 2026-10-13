"""Dish pictures, served without sign-in so the student QR page and offline kitchen screens can
show them. URLs carry the picture's version, so browsers cache them for a year."""

from __future__ import annotations

import uuid

from fastapi import APIRouter
from fastapi.responses import FileResponse

from foodwise.core import jobs
from foodwise.core.errors import NotFound
from foodwise.core.storage import storage
from foodwise.db.base import system_session
from foodwise.db.models import Dish

router = APIRouter(prefix="/media", tags=["media"])


@router.get("/dishes/{dish_id}", response_class=FileResponse)
def dish_image(dish_id: uuid.UUID) -> FileResponse:
    with system_session() as db:
        d = db.get(Dish, dish_id)
        key = d.image_key if d is not None and d.image_status == "found" else None
        if key and not storage().exists(key):
            # The file is gone (new storage volume, restored database): look for the picture again.
            jobs.enqueue(db, "find_dish_image", {"dish_id": str(d.id), "force": True}, tenant_id=d.tenant_id,
                         dedupe_key=f"dish-image:{d.id}")
            db.commit()
            key = None
    if not key:
        raise NotFound("No picture for this dish.")
    return FileResponse(storage().local_path(key), media_type="image/jpeg",
                        headers={"Cache-Control": "public, max-age=31536000, immutable"})
