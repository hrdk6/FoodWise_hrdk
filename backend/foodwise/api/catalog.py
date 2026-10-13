"""Canteens, dishes, menus, the academic calendar and weather."""

from __future__ import annotations

import uuid
from datetime import date, time, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, File, Query, UploadFile
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, select

from foodwise.api.common import Ok
from foodwise.core import audit, jobs
from foodwise.core.deps import Ctx, need
from foodwise.core.errors import Conflict, NotFound, Unprocessable
from foodwise.db.models import CalendarDay, Campus, Canteen, Dish, Forecast, MenuEntry, WeatherDay
from foodwise.services import dish_images

router = APIRouter(tags=["catalog"])

Course = Literal["breakfast", "main", "rice", "dal", "curry", "sabzi", "bread", "side", "dessert", "snack", "beverage"]
Diet = Literal["veg", "non_veg", "egg", "jain"]
Unit = Literal["plates", "kg", "litres", "pieces"]
FoodCategory = Literal["cooked_rice", "curry_gravy", "dal", "bread", "fried_snack", "dessert_dairy", "salad_raw", "beverage"]


class Meal(BaseModel):
    key: str = Field(pattern=r"^[a-z_]{2,20}$")
    label: str = Field(min_length=1, max_length=40)
    start: str = Field(pattern=r"^\d{2}:\d{2}$")
    end: str = Field(pattern=r"^\d{2}:\d{2}$")


class CanteenOut(BaseModel):
    id: uuid.UUID
    name: str
    kind: str
    service_mode: str
    lat: float
    lon: float
    pickup_point: str | None
    cutoff_time: time
    meals: list[Meal]
    expected_diners: int
    costs: dict | None
    is_active: bool


class CanteenIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["mess", "canteen"] = "mess"
    service_mode: Literal["fixed_thali", "choice", "counter"] = "fixed_thali"
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    pickup_point: str | None = Field(default=None, max_length=300)
    cutoff_time: time = time(21, 30)
    meals: list[Meal] = Field(min_length=1, max_length=6)
    expected_diners: int = Field(ge=1, le=50000)
    costs: dict | None = None


class CanteenPatch(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    pickup_point: str | None = Field(default=None, max_length=300)
    cutoff_time: time | None = None
    meals: list[Meal] | None = None
    expected_diners: int | None = Field(default=None, ge=1, le=50000)
    shortage_to_waste_ratio: float | None = Field(default=None, ge=0.1, le=50)
    is_active: bool | None = None


def _canteen_out(c: Canteen) -> CanteenOut:
    return CanteenOut(id=c.id, name=c.name, kind=c.kind, service_mode=c.service_mode, lat=c.lat, lon=c.lon,
                      pickup_point=c.pickup_point, cutoff_time=c.cutoff_time, meals=c.meals,
                      expected_diners=c.expected_diners, costs=c.costs, is_active=c.is_active)


@router.get("/canteens", response_model=list[CanteenOut])
def list_canteens(ctx: Ctx = Depends(need("tenant:read"))) -> list[CanteenOut]:
    q = select(Canteen).order_by(Canteen.name)
    if ctx.principal.canteen_ids:
        q = q.where(Canteen.id.in_(ctx.principal.canteen_ids))
    return [_canteen_out(c) for c in ctx.db.execute(q).scalars()]


@router.post("/canteens", response_model=CanteenOut, status_code=201)
def create_canteen(body: CanteenIn, ctx: Ctx = Depends(need("tenant:write"))) -> CanteenOut:
    campus = ctx.db.execute(select(Campus).limit(1)).scalar_one_or_none()
    if campus is None:
        campus = Campus(tenant_id=ctx.tenant_id, name="Main campus", lat=body.lat, lon=body.lon)
        ctx.db.add(campus)
        ctx.db.flush()
    c = Canteen(tenant_id=ctx.tenant_id, campus_id=campus.id, **body.model_dump(exclude={"meals"}),
                meals=[m.model_dump() for m in body.meals])
    ctx.db.add(c)
    ctx.db.flush()
    audit.record(ctx.db, ctx.principal, "canteen.created", target_type="canteen", target_id=c.id)
    ctx.db.commit()
    return _canteen_out(c)


@router.patch("/canteens/{canteen_id}", response_model=CanteenOut)
def patch_canteen(canteen_id: uuid.UUID, body: CanteenPatch, ctx: Ctx = Depends(need("tenant:write"))) -> CanteenOut:
    c = ctx.db.get(Canteen, canteen_id)
    if c is None:
        raise NotFound("Canteen not found.")
    changes = body.model_dump(exclude_none=True)
    if "shortage_to_waste_ratio" in changes:
        c.costs = {**(c.costs or {}), "shortage_to_waste_ratio": changes.pop("shortage_to_waste_ratio")}
    if "meals" in changes:
        c.meals = [m.model_dump() for m in body.meals or []]
        changes.pop("meals")
    for k, v in changes.items():
        setattr(c, k, v)
    audit.record(ctx.db, ctx.principal, "canteen.updated", target_type="canteen", target_id=c.id,
                 meta={"fields": sorted(body.model_dump(exclude_none=True))})
    ctx.db.commit()
    return _canteen_out(c)


# --------------------------------------------------------------------------- dishes


class DishOut(BaseModel):
    id: uuid.UUID
    name: str
    name_hi: str | None
    aliases: list[str]
    course: str
    diet: str
    cuisine: str | None
    unit: str
    portion_kg: float
    cost_per_unit: float
    food_category: str
    allergens: list[str]
    is_active: bool
    image_url: str | None
    image_status: str
    image_source: str | None
    image_credit: str | None
    image_source_url: str | None


class DishIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    name_hi: str | None = Field(default=None, max_length=120)
    aliases: list[str] = []
    course: Course
    diet: Diet = "veg"
    cuisine: str | None = Field(default=None, max_length=40)
    unit: Unit = "plates"
    portion_kg: float = Field(default=0.25, gt=0, le=5)
    cost_per_unit: float = Field(default=30, ge=0, le=10000)
    food_category: FoodCategory = "curry_gravy"
    allergens: list[str] = []

    @field_validator("aliases", "allergens")
    @classmethod
    def _trim(cls, v: list[str]) -> list[str]:
        return [x.strip()[:60] for x in v if x.strip()][:20]


class DishPatch(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    name_hi: str | None = Field(default=None, max_length=120)
    aliases: list[str] | None = None
    course: Course | None = None
    diet: Diet | None = None
    unit: Unit | None = None
    portion_kg: float | None = Field(default=None, gt=0, le=5)
    cost_per_unit: float | None = Field(default=None, ge=0, le=10000)
    food_category: FoodCategory | None = None
    allergens: list[str] | None = None
    is_active: bool | None = None


def _dish_out(d: Dish) -> DishOut:
    return DishOut(id=d.id, name=d.name, name_hi=d.name_hi, aliases=list(d.aliases or []), course=d.course,
                   diet=d.diet, cuisine=d.cuisine, unit=d.unit, portion_kg=d.portion_kg, cost_per_unit=d.cost_per_unit,
                   food_category=d.food_category, allergens=list(d.allergens or []), is_active=d.is_active,
                   image_url=dish_images.image_url(d), image_status=d.image_status or "pending",
                   image_source=d.image_source, image_credit=d.image_credit, image_source_url=d.image_source_url)


@router.get("/dishes", response_model=list[DishOut])
def list_dishes(ctx: Ctx = Depends(need("tenant:read"))) -> list[DishOut]:
    return [_dish_out(d) for d in ctx.db.execute(select(Dish).order_by(Dish.name)).scalars()]


@router.post("/dishes", response_model=DishOut, status_code=201)
def create_dish(body: DishIn, ctx: Ctx = Depends(need("menu:write"))) -> DishOut:
    if ctx.db.execute(select(Dish.id).where(Dish.name == body.name)).first():
        raise Conflict("A dish with that name already exists.")
    d = Dish(tenant_id=ctx.tenant_id, **body.model_dump())
    ctx.db.add(d)
    ctx.db.flush()
    audit.record(ctx.db, ctx.principal, "dish.created", target_type="dish", target_id=d.id)
    jobs.enqueue(ctx.db, "find_dish_image", {"dish_id": str(d.id)}, tenant_id=ctx.tenant_id, dedupe_key=f"dish-image:{d.id}")
    ctx.db.commit()
    return _dish_out(d)


@router.patch("/dishes/{dish_id}", response_model=DishOut)
def patch_dish(dish_id: uuid.UUID, body: DishPatch, ctx: Ctx = Depends(need("menu:write"))) -> DishOut:
    d = ctx.db.get(Dish, dish_id)
    if d is None:
        raise NotFound("Dish not found.")
    renamed = body.name is not None and body.name != d.name
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(d, k, v)
    if renamed and d.image_source != "upload":
        d.image_status = "pending"
        jobs.enqueue(ctx.db, "find_dish_image", {"dish_id": str(d.id), "force": True}, tenant_id=ctx.tenant_id,
                     dedupe_key=f"dish-image:{d.id}")
    ctx.db.commit()
    return _dish_out(d)


# --------------------------------------------------------------------------- dish pictures


class ImageCandidate(BaseModel):
    title: str
    thumb_url: str
    width: int
    height: int
    license: str
    credit: str
    page_url: str
    score: float


def _dish(ctx: Ctx, dish_id: uuid.UUID) -> Dish:
    d = ctx.db.get(Dish, dish_id)
    if d is None:
        raise NotFound("Dish not found.")
    return d


@router.get("/dishes/{dish_id}/image/candidates", response_model=list[ImageCandidate])
def image_candidates(dish_id: uuid.UUID, q: str | None = Query(None, max_length=80),
                     ctx: Ctx = Depends(need("menu:write"))) -> list[ImageCandidate]:
    """Freely licensed photos on Wikimedia Commons that match the dish (or your own search words)."""
    d = _dish(ctx, dish_id)
    try:
        found = dish_images.candidates([q] if q else [d.name, *(d.aliases or [])], limit=12)
    except Exception as exc:  # noqa: BLE001
        raise Unprocessable("Could not reach Wikimedia Commons just now. Try again, or upload a photo.") from exc
    return [ImageCandidate(title=c.title, thumb_url=c.thumb_url, width=c.width, height=c.height, license=c.license,
                           credit=c.credit, page_url=c.page_url, score=c.score) for c in found]


class ChooseImageIn(BaseModel):
    title: str = Field(min_length=6, max_length=300, pattern=r"^File:")


@router.post("/dishes/{dish_id}/image", response_model=DishOut)
def choose_image(dish_id: uuid.UUID, body: ChooseImageIn, ctx: Ctx = Depends(need("menu:write"))) -> DishOut:
    """Use one of the candidate photos (by its Commons file title)."""
    d = _dish(ctx, dish_id)
    try:
        picked = dish_images.candidate_for(body.title)
        if picked is None:
            raise Unprocessable("That picture is not on Wikimedia Commons, or it is not freely licensed.")
        dish_images.use_candidate(d, picked)
    except Unprocessable:
        raise
    except Exception as exc:  # noqa: BLE001
        raise Unprocessable("Could not download that picture just now. Try again.") from exc
    audit.record(ctx.db, ctx.principal, "dish.image_chosen", target_type="dish", target_id=d.id, meta={"title": body.title})
    ctx.db.commit()
    return _dish_out(d)


@router.put("/dishes/{dish_id}/image/upload", response_model=DishOut)
async def upload_image(dish_id: uuid.UUID, file: UploadFile = File(...), ctx: Ctx = Depends(need("menu:write"))) -> DishOut:
    """Your own photo of the dish. It is cropped square and its metadata (including location) removed."""
    d = _dish(ctx, dish_id)
    data = await file.read(dish_images.MAX_BYTES + 1)
    dish_images.store(d, data, source="upload", credit="Photo by the kitchen", licence=None, source_url=None)
    audit.record(ctx.db, ctx.principal, "dish.image_uploaded", target_type="dish", target_id=d.id)
    ctx.db.commit()
    return _dish_out(d)


@router.delete("/dishes/{dish_id}/image", response_model=DishOut)
def remove_image(dish_id: uuid.UUID, ctx: Ctx = Depends(need("menu:write"))) -> DishOut:
    """No picture for this dish; FoodWise will not look for one again."""
    d = _dish(ctx, dish_id)
    d.image_key = d.image_source = d.image_credit = d.image_license = d.image_source_url = None
    d.image_status = "removed"
    ctx.db.commit()
    return _dish_out(d)


@router.post("/dishes/{dish_id}/image/auto", response_model=DishOut)
def auto_image(dish_id: uuid.UUID, ctx: Ctx = Depends(need("menu:write"))) -> DishOut:
    """Look for a picture again in the background."""
    d = _dish(ctx, dish_id)
    d.image_status = "pending"
    jobs.enqueue(ctx.db, "find_dish_image", {"dish_id": str(d.id), "force": True}, tenant_id=ctx.tenant_id,
                 dedupe_key=f"dish-image:{d.id}")
    ctx.db.commit()
    return _dish_out(d)


# --------------------------------------------------------------------------- menus


class MenuDay(BaseModel):
    date: date
    meals: dict[str, list[uuid.UUID]]


class MenuPut(BaseModel):
    canteen_id: uuid.UUID
    date: date
    meal: str
    dish_ids: list[uuid.UUID] = Field(max_length=30)


@router.get("/menus", response_model=list[MenuDay])
def get_menus(canteen_id: uuid.UUID, start: date, end: date, ctx: Ctx = Depends(need("tenant:read"))) -> list[MenuDay]:
    ctx.check_canteen(canteen_id)
    if (end - start).days > 62:
        raise Unprocessable("Ask for at most two months at a time.")
    rows = ctx.db.execute(
        select(MenuEntry.service_date, MenuEntry.meal, MenuEntry.dish_id)
        .where(MenuEntry.canteen_id == canteen_id, MenuEntry.service_date >= start, MenuEntry.service_date <= end)
        .order_by(MenuEntry.service_date)
    ).all()
    days: dict[date, dict[str, list[uuid.UUID]]] = {}
    for d, meal, dish in rows:
        days.setdefault(d, {}).setdefault(meal, []).append(dish)
    return [MenuDay(date=d, meals=m) for d, m in sorted(days.items())]


@router.put("/menus", response_model=MenuDay)
def put_menu(body: MenuPut, ctx: Ctx = Depends(need("menu:write"))) -> MenuDay:
    ctx.check_canteen(body.canteen_id)
    canteen = ctx.db.get(Canteen, body.canteen_id)
    if canteen is None:
        raise NotFound("Canteen not found.")
    if body.meal not in {m["key"] for m in canteen.meals}:
        raise Unprocessable(f"{canteen.name} does not serve '{body.meal}'.")
    ctx.db.execute(delete(MenuEntry).where(MenuEntry.canteen_id == body.canteen_id, MenuEntry.service_date == body.date,
                                           MenuEntry.meal == body.meal))
    for dish_id in dict.fromkeys(body.dish_ids):
        ctx.db.add(MenuEntry(tenant_id=ctx.tenant_id, canteen_id=body.canteen_id, service_date=body.date,
                             meal=body.meal, dish_id=dish_id))
    # Keep the prep plan in step with the menu: drop forecasts for dishes no longer served and
    # re-forecast the day in the background.
    ctx.db.execute(delete(Forecast).where(Forecast.canteen_id == body.canteen_id, Forecast.service_date == body.date,
                                          Forecast.meal == body.meal, Forecast.dish_id.not_in(body.dish_ids or [uuid.uuid4()])))
    if body.date >= date.today() - timedelta(days=1):
        jobs.enqueue(ctx.db, "generate_forecasts", {"canteen_id": str(body.canteen_id), "date": body.date.isoformat()},
                     tenant_id=ctx.tenant_id, dedupe_key=f"forecast:{body.canteen_id}:{body.date}")
    audit.record(ctx.db, ctx.principal, "menu.updated", target_type="canteen", target_id=body.canteen_id,
                 meta={"date": body.date.isoformat(), "meal": body.meal, "dishes": len(body.dish_ids)})
    ctx.db.commit()
    return get_menus(body.canteen_id, body.date, body.date, ctx)[0] if body.dish_ids else MenuDay(date=body.date, meals={})


# --------------------------------------------------------------------------- calendar and weather


class CalendarOut(BaseModel):
    id: uuid.UUID
    day: date
    kind: str
    label: str
    canteen_id: uuid.UUID | None
    meal: str | None
    expected_attendance: int | None
    disruption: str | None


class CalendarIn(BaseModel):
    day: date
    kind: Literal["exam", "holiday", "vacation", "fest", "event", "term_start"]
    label: str = Field(min_length=1, max_length=200)
    canteen_id: uuid.UUID | None = None
    meal: str | None = None
    expected_attendance: int | None = Field(default=None, ge=0, le=100000)
    days: int = Field(default=1, ge=1, le=90)


def _cal_out(c: CalendarDay) -> CalendarOut:
    return CalendarOut(id=c.id, day=c.day, kind=c.kind, label=c.label, canteen_id=c.canteen_id, meal=c.meal,
                       expected_attendance=c.expected_attendance, disruption=c.disruption)


@router.get("/calendar", response_model=list[CalendarOut])
def list_calendar(start: date, end: date, ctx: Ctx = Depends(need("tenant:read"))) -> list[CalendarOut]:
    rows = ctx.db.execute(
        select(CalendarDay).where(CalendarDay.day >= start, CalendarDay.day <= end).order_by(CalendarDay.day)
    ).scalars()
    return [_cal_out(c) for c in rows]


@router.post("/calendar", response_model=list[CalendarOut], status_code=201)
def add_calendar(body: CalendarIn, ctx: Ctx = Depends(need("calendar:write"))) -> list[CalendarOut]:
    out = []
    for i in range(body.days):
        c = CalendarDay(tenant_id=ctx.tenant_id, day=body.day + timedelta(days=i), kind=body.kind, label=body.label,
                        canteen_id=body.canteen_id, meal=body.meal, expected_attendance=body.expected_attendance)
        ctx.db.add(c)
        out.append(c)
    ctx.db.flush()
    audit.record(ctx.db, ctx.principal, "calendar.added", meta={"kind": body.kind, "label": body.label, "days": body.days})
    ctx.db.commit()
    return [_cal_out(c) for c in out]


@router.delete("/calendar/{entry_id}", response_model=Ok)
def delete_calendar(entry_id: uuid.UUID, ctx: Ctx = Depends(need("calendar:write"))) -> Ok:
    c = ctx.db.get(CalendarDay, entry_id)
    if c is None:
        raise NotFound("Calendar entry not found.")
    ctx.db.delete(c)
    ctx.db.commit()
    return Ok()


class WeatherOut(BaseModel):
    day: date
    temp_c: float
    rain_prob: float
    condition: str
    source: str


@router.get("/weather", response_model=list[WeatherOut])
def list_weather(canteen_id: uuid.UUID, start: date, end: date = Query(...),
                 ctx: Ctx = Depends(need("tenant:read"))) -> list[WeatherOut]:
    ctx.check_canteen(canteen_id)
    rows = ctx.db.execute(
        select(WeatherDay).where(WeatherDay.canteen_id == canteen_id, WeatherDay.day >= start, WeatherDay.day <= end)
        .order_by(WeatherDay.day)
    ).scalars()
    return [WeatherOut(day=w.day, temp_c=w.temp_c, rain_prob=w.rain_prob, condition=w.condition, source=w.source) for w in rows]
