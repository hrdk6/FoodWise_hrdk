"""Seed a demo college by running FoodWise against the simulated world.

Timeline (relative to today):
  -240 days .. go-live   the kitchen plans by hand (last week's number plus a buffer); history is
                         loaded as an import, the way a real college would onboard
  go-live                the first model is trained on that history and becomes champion
  go-live .. yesterday   every evening FoodWise forecasts the next day, the manager approves or
                         overrides, and the kitchen cooks that amount against the world's true demand
  -7 days                a retrain produces a challenger, which then shadows the champion on days
                         it has never seen; the shadow result decides promotion
  today, tomorrow        live plans: today's approved, tomorrow's awaiting approval

So the waste reduction, accuracy and model decisions shown in the demo are outputs of the system
running on simulated reality, not numbers written into the database.
"""

from __future__ import annotations

import logging
import math
import time
import uuid
from datetime import UTC, date, datetime, timedelta
from datetime import time as dtime

from sqlalchemy import delete, insert, select, text
from sqlalchemy.orm import Session

from foodwise.config import get_settings
from foodwise.core import jobs, security
from foodwise.core.timeutil import at_local, local_today
from foodwise.db.base import system_session, tenant_session
from foodwise.db.models import (
    AuditLog,
    CalendarDay,
    Campus,
    Canteen,
    Dish,
    Donation,
    Feedback,
    Forecast,
    ImportJob,
    IncidentEvent,
    MenuEntry,
    Rsvp,
    ServiceLog,
    SurplusIncident,
    Tenant,
    User,
    Vote,
    WeatherDay,
)
from foodwise.domain.textanalysis import analyze
from foodwise.ml import monitoring, serving, training
from foodwise.sim.world import CANTEENS, DISH_BY_KEY, DISHES, CanteenDef, World, rng, stable_int

log = logging.getLogger("foodwise.seed")

# Development-only credentials. `foodwise seed` refuses to run when FOODWISE_ENV=production,
# and the API refuses to start in production if any seeded account exists.
DEMO_PASSWORD = "foodwise-demo"
GO_LIVE_DAYS_AGO = 21
CHALLENGER_DAYS_AGO = 7
TZ = "Asia/Kolkata"

# One shared organisation login per college, and one FoodWise admin.
ORG_EMAIL = "team@sahyadri.example"
ORG_NAME = "Sahyadri kitchen team"
ADMIN_EMAIL = "admin@foodwise.example"

# Past rescues in the demo went to these places. They are fictional (marked `demo:`), so they are
# never suggested as real nearby places; live suggestions come from OpenStreetMap.
DEMO_PLACES = {
    "annapurna": ("Annapurna Community Kitchen (demo)", "community_kitchen", 18.5089, 73.8567),
    "sahara": ("Sahara Night Shelter (demo)", "shelter", 18.5305, 73.8475),
    "roti": ("Roti Bank Deccan (demo)", "food_bank", 18.5362, 73.8402),
    "stars": ("Little Stars Children's Home (demo)", "children_home", 18.5018, 73.8650),
    "asha": ("Asha Women's Shelter (demo)", "shelter", 18.5260, 73.8740),
    "goshala": ("Gau Seva Goshala (demo)", "animal_shelter", 18.4600, 73.8200),
}

OVERRIDE_REASONS_UP = [
    "Guest lecture in the auditorium, expecting extra diners",
    "Hostel block returning from field trip",
    "Safety buffer, last week this ran out",
    "Sports practice ends just before dinner",
]
OVERRIDE_REASONS_DOWN = [
    "Many students leaving for the long weekend",
    "Department trip, one batch away",
]


def _bulk(db: Session, model, rows: list[dict], chunk: int = 5000) -> None:
    """Core INSERT ... VALUES batches. (The ORM bulk path splits batches whenever optional
    columns are None, which made feedback rows insert one at a time.)"""
    table = model.__table__
    for i in range(0, len(rows), chunk):
        db.execute(insert(table), [r if "id" in r else {**r, "id": uuid.uuid4()} for r in rows[i : i + chunk]])


class Seeder:
    def __init__(self, today: date | None = None, quick: bool = False):
        self.today = today or local_today(TZ)
        self.world = World(self.today)
        self.go_live = self.today - timedelta(days=GO_LIVE_DAYS_AGO)
        self.challenger_day = self.today - timedelta(days=CHALLENGER_DAYS_AGO)
        self.quick = quick
        self.dish_ids: dict[str, uuid.UUID] = {}
        self.canteen_ids: dict[str, uuid.UUID] = {}
        self.org_user: uuid.UUID | None = None
        self.truth: dict[tuple, float] = {}
        self.footfalls: dict[tuple, int] = {}

    # ------------------------------------------------------------------ entry point

    def run(self) -> dict:
        if get_settings().env == "production":
            raise RuntimeError("Refusing to seed demo data in production.")
        t0 = time.perf_counter()
        self._structure()
        log.info("structure ready (%.1fs)", time.perf_counter() - t0)
        self._history_before_go_live()
        log.info("manual-era history logged (%.1fs)", time.perf_counter() - t0)
        self._students()
        log.info("feedback, RSVPs and votes seeded (%.1fs)", time.perf_counter() - t0)
        results = self._run_foodwise()
        log.info("FoodWise era simulated (%.1fs)", time.perf_counter() - t0)
        self._incidents_history()
        self._riverside()
        self._queue_pictures()
        log.info("seed complete in %.1fs", time.perf_counter() - t0)
        return {"tenant_id": str(self.tenant_id), "seconds": round(time.perf_counter() - t0, 1), **results}

    # ------------------------------------------------------------------ structure

    def _structure(self) -> None:
        w = self.world
        with system_session() as db:
            tenant = Tenant(
                name="Sahyadri Institute of Technology",
                slug="sahyadri",
                timezone=TZ,
                currency="INR",
                service_mode="fixed_thali",
                data_sharing="none",
                feedback_mode="anonymous",
                is_demo=True,
                settings={"safety": {"reviewed": True, "reviewed_by": "Food safety officer, Sahyadri Institute"},
                          "demo": {"anchor": self.today.isoformat()}},
            )
            db.add(tenant)
            db.flush()
            self.tenant_id = tenant.id
            campus = Campus(tenant_id=tenant.id, name="Main campus", address="University Road, Pune", lat=18.5204, lon=73.8540)
            db.add(campus)
            db.flush()
            for c in CANTEENS:
                canteen = Canteen(
                    tenant_id=tenant.id, campus_id=campus.id, name=c.name, kind=c.kind, service_mode=c.service_mode,
                    lat=c.lat, lon=c.lon, pickup_point=c.pickup_point, cutoff_time=dtime(21, 30),
                    meals=[dict(m) for m in c.meals], expected_diners=c.diners,
                    costs={"shortage_to_waste_ratio": 9.0},
                )
                db.add(canteen)
                db.flush()
                self.canteen_ids[c.key] = canteen.id
            for d in DISHES:
                dish = Dish(
                    tenant_id=tenant.id, name=d.name, name_hi=d.name_hi, aliases=list(d.aliases), course=d.course,
                    diet=d.diet, cuisine=d.cuisine, unit=d.unit, portion_kg=d.portion_kg, cost_per_unit=d.cost,
                    food_category=d.category, allergens=list(d.allergens),
                )
                db.add(dish)
                db.flush()
                self.dish_ids[d.key] = dish.id

            pw = security.hash_password(DEMO_PASSWORD)
            org = User(tenant_id=tenant.id, email=ORG_EMAIL, name=ORG_NAME, role="organization", password_hash=pw,
                       is_seed=True)
            db.add(org)
            db.add(User(tenant_id=None, email=ADMIN_EMAIL, name="FoodWise engineering", role="admin",
                        password_hash=pw, is_seed=True))
            db.flush()
            self.org_user = org.id

            cal_rows = []
            for e in w.events:
                if w.start <= e.day <= w.end:
                    cal_rows.append({
                        "tenant_id": tenant.id, "canteen_id": self.canteen_ids.get(e.canteen) if e.canteen else None,
                        "day": e.day, "kind": e.kind, "label": e.label, "meal": e.meal,
                        "expected_attendance": e.attendance, "disruption": e.disruption,
                    })
            _bulk(db, CalendarDay, cal_rows)

            weather_rows, menu_rows = [], []
            day = w.start
            while day <= w.end:
                temp, prob, _ = w.weather(day)
                for c in CANTEENS:
                    weather_rows.append({"tenant_id": tenant.id, "canteen_id": self.canteen_ids[c.key], "day": day,
                                         "temp_c": temp, "rain_prob": prob,
                                         "condition": "rain" if prob >= 0.6 else "cloudy" if prob >= 0.3 else "clear",
                                         "source": "simulated"})
                    for meal, dishes in w.menu(c, day).items():
                        for dk in dishes:
                            menu_rows.append({"tenant_id": tenant.id, "canteen_id": self.canteen_ids[c.key],
                                              "service_date": day, "meal": meal, "dish_id": self.dish_ids[dk]})
                day += timedelta(days=1)
            _bulk(db, WeatherDay, weather_rows)
            _bulk(db, MenuEntry, menu_rows)
            db.commit()

        # True demand for every service up to tomorrow (RSVPs for tomorrow derive from it).
        day = w.start
        while day <= self.today + timedelta(days=1):
            for c in CANTEENS:
                for meal, dishes in w.menu(c, day).items():
                    ff = w.footfall(c, day, meal)
                    self.footfalls[(c.key, day, meal)] = ff
                    for dk in dishes:
                        self.truth[(c.key, day, meal, dk)] = w.demand(c, day, meal, dk, ff, dishes)
            day += timedelta(days=1)

    # ------------------------------------------------------------------ manual era

    def _log_row(self, c: CanteenDef, day: date, meal: str, dk: str, prepared: float, source: str,
                 import_job: uuid.UUID | None = None) -> dict:
        d = DISH_BY_KEY[dk]
        demand = self.truth[(c.key, day, meal, dk)]
        served = min(demand, prepared)
        sold_out = demand > prepared + (0.25 if d.unit in ("kg", "litres") else 0.5)
        r = rng(self.world.seed, "waste", c.key, day.toordinal(), meal, dk)
        q = self.world.quality(dk, day)
        plate_rate = float(min(0.2, max(0.01, 0.06 - 0.04 * q + r.normal(0, 0.01))))
        unserved = max(0.0, prepared - served)
        spoil = round(unserved * 0.1, 1) if r.random() < 0.02 else 0.0
        meal_def = next(m for m in c.meals if m["key"] == meal)
        cooked = at_local(day, meal_def["start"], TZ) - timedelta(minutes=75)
        rnd = (lambda v: round(v, 1)) if d.unit in ("kg", "litres") else (lambda v: float(round(v)))
        return {
            "tenant_id": self.tenant_id, "canteen_id": self.canteen_ids[c.key], "service_date": day, "meal": meal,
            "dish_id": self.dish_ids[dk], "unit": d.unit, "prepared_qty": rnd(prepared), "served_qty": rnd(served),
            "unserved_qty": rnd(unserved - spoil), "plate_waste_qty": rnd(served * plate_rate), "spoilage_qty": spoil,
            "footfall": self.footfalls[(c.key, day, meal)], "sold_out": bool(sold_out), "cooked_at": cooked,
            "source": source, "import_job_id": import_job, "quality_flags": [],
            "created_by": self.org_user if source == "kitchen" else None,
        }

    def _history_before_go_live(self) -> None:
        w = self.world
        job_id = uuid.uuid4()
        rows = []
        last: dict[tuple, tuple[float, bool]] = {}
        day = w.start
        while day < self.go_live:
            for c in CANTEENS:
                for meal, dishes in w.menu(c, day).items():
                    for dk in dishes:
                        lw = last.get((c.key, meal, dk, (day - timedelta(days=7)).toordinal()))
                        prepared = w.manual_prep(c, day, meal, dk, dishes, lw[0] if lw else None, bool(lw and lw[1]))
                        row = self._log_row(c, day, meal, dk, prepared, "import", job_id)
                        rows.append(row)
                        last[(c.key, meal, dk, day.toordinal())] = (row["served_qty"], row["sold_out"])
            day += timedelta(days=1)
        with system_session() as db:
            db.add(ImportJob(
                id=job_id, tenant_id=self.tenant_id, filename="mess_and_canteen_history.csv",
                storage_key=f"tenants/{self.tenant_id}/imports/{job_id}.csv", status="committed",
                headers=["date", "canteen", "meal", "dish", "unit", "prepared_qty", "served_qty", "leftover_qty",
                         "footfall", "sold_out"],
                mapping={}, row_count=len(rows), committed_rows=len(rows),
                report={"score": 96.4, "counts": {"warning": 11, "info": 1}, "days": (self.go_live - w.start).days},
                created_by=self.org_user,
                committed_at=datetime.combine(w.start, dtime(10), tzinfo=UTC) + timedelta(days=(self.go_live - w.start).days - 1),
            ))
            _bulk(db, ServiceLog, rows)
            db.commit()

    # ------------------------------------------------------------------ students

    def _students(self) -> None:
        w = self.world
        tid = self.tenant_id
        feedback_rows, rsvp_rows, vote_rows = [], [], []
        fb_start = max(w.start, self.today - timedelta(days=95))
        rsvp_start = self.today - timedelta(days=48)
        residents = [security.hash_device(tid, f"sim-device-{i}") for i in range(1300)]
        day = fb_start
        while day <= self.today + timedelta(days=1):
            for c in CANTEENS:
                for meal, dishes in w.menu(c, day).items():
                    ff = self.footfalls[(c.key, day, meal)]
                    if day < self.today:
                        for dk in dishes:
                            for k, fb in enumerate(w.feedback(c, day, meal, dk, ff)):
                                device = residents[(stable_int(dk, meal) + k * 37 + day.toordinal()) % len(residents)]
                                analysis = analyze(fb["text"]) if fb["text"] else None
                                created = at_local(day, next(m for m in c.meals if m["key"] == meal)["end"], TZ)
                                feedback_rows.append({
                                    "tenant_id": tid, "canteen_id": self.canteen_ids[c.key], "service_date": day,
                                    "meal": meal, "dish_id": self.dish_ids[dk], "rating": fb["rating"],
                                    "aspects": fb["aspects"], "portion": fb["portion"],
                                    "text": analysis.clean_text if analysis else None,
                                    "text_expires_at": created + timedelta(days=90) if analysis else None,
                                    "language": analysis.language if analysis else None,
                                    "sentiment": analysis.sentiment if analysis else None,
                                    "text_aspects": analysis.aspects if analysis else {},
                                    "device_hash": f"{device[:40]}{k:04d}{dk[:4]}", "spam_score": 0.0, "weight": 1.0,
                                    "created_at": created,
                                })
                    if day >= rsvp_start and c.kind == "mess":
                        eating, skipping = w.rsvp(c, day, meal, ff)
                        r = rng(w.seed, "rsvp-pick", c.key, day.toordinal(), meal)
                        picks = r.permutation(len(residents))[: eating + skipping]
                        for j, idx in enumerate(picks):
                            rsvp_rows.append({
                                "tenant_id": tid, "canteen_id": self.canteen_ids[c.key], "service_date": day,
                                "meal": meal, "device_hash": residents[idx], "status": "eating" if j < eating else "skipping",
                            })
            day += timedelta(days=1)
        # Weekly "vote for next week's special" at North Mess.
        week = self.today - timedelta(days=self.today.weekday()) - timedelta(weeks=7)
        candidates = ["veg_biryani", "chole_bhature", "pav_bhaji", "dal_makhani"]
        while week <= self.today:
            r = rng(w.seed, "votes", week.toordinal())
            n = int(r.integers(140, 260))
            prefs = [0.36, 0.27, 0.24, 0.13]
            for i in range(n):
                vote_rows.append({"tenant_id": tid, "canteen_id": self.canteen_ids["north_mess"], "week_start": week,
                                  "dish_id": self.dish_ids[candidates[int(r.choice(4, p=prefs))]],
                                  "device_hash": residents[(i * 7 + week.toordinal()) % len(residents)]})
            week += timedelta(weeks=1)
        with system_session() as db:
            _bulk(db, Feedback, feedback_rows)
            _bulk(db, Rsvp, rsvp_rows)
            _bulk(db, Vote, vote_rows)
            db.commit()

    # ------------------------------------------------------------------ the FoodWise era

    def _run_foodwise(self) -> dict:
        out: dict = {"models": {}}
        for c in CANTEENS:
            res = training.train_canteen(self.tenant_id, self.canteen_ids[c.key], as_of=self.go_live, trigger="onboarding")
            out["models"][c.key] = [res]
        manager = self.org_user
        day = self.go_live
        while day < self.today:
            if day == self.challenger_day:
                for c in CANTEENS:
                    res = training.train_canteen(self.tenant_id, self.canteen_ids[c.key], as_of=day, trigger="weekly retrain")
                    out["models"][c.key].append(res)
            logs = []
            with tenant_session(self.tenant_id) as db:
                tenant = db.get(Tenant, self.tenant_id)
                for c in CANTEENS:
                    canteen = db.get(Canteen, self.canteen_ids[c.key])
                    forecasts = serving.generate(db, tenant, canteen, day)
                    for f in forecasts:
                        dk = next(k for k, v in self.dish_ids.items() if v == f.dish_id)
                        qty = self._decide(f, day, manager)
                        # Kitchens cook to the plan, rounding batches up rather than down.
                        r = rng(self.world.seed, "cook", c.key, day.toordinal(), f.meal, dk)
                        cooked = qty * (1 + abs(r.normal(0, 0.01)))
                        unit = DISH_BY_KEY[dk].unit
                        cooked = math.ceil(cooked * 2) / 2 if unit in ("kg", "litres") else float(math.ceil(cooked))
                        logs.append(self._log_row(c, day, f.meal, dk, cooked, "kitchen"))
                db.commit()
            with system_session() as db:
                _bulk(db, ServiceLog, logs)
                db.commit()
            day += timedelta(days=1)

        with tenant_session(self.tenant_id) as db:
            tenant = db.get(Tenant, self.tenant_id)
            decisions = {}
            for c in CANTEENS:
                decisions[c.key] = monitoring.evaluate_shadow(db, self.canteen_ids[c.key], self.today)
            db.commit()
            out["shadow"] = decisions
            for c in CANTEENS:
                canteen = db.get(Canteen, self.canteen_ids[c.key])
                for day in (self.today, self.today + timedelta(days=1)):
                    forecasts = serving.generate(db, tenant, canteen, day)
                    if day == self.today:
                        for f in forecasts:
                            f.status, f.decided_by = "approved", manager
                            f.decided_at = at_local(day - timedelta(days=1), "21:40", TZ)
                champ = training.registry.champion(db, canteen.id)
                if champ is not None:
                    champ.monitoring = monitoring.evaluate_canteen(db, canteen.id, self.today)
            db.add(AuditLog(tenant_id=self.tenant_id, actor_id=None, actor_label="system", action="seed.completed",
                            meta={"go_live": self.go_live.isoformat()}))
            db.commit()
        return out

    def _decide(self, f: Forecast, day: date, manager: uuid.UUID) -> float:
        r = rng(self.world.seed, "decide", str(f.canteen_id), day.toordinal(), f.meal, str(f.dish_id))
        roll = r.random()
        f.decided_by = manager
        f.decided_at = at_local(day - timedelta(days=1), "21:40", TZ)
        if roll < 0.12:
            f.status = "overridden"
            f.override_qty = self._round(f.recommended_qty * r.uniform(1.08, 1.22), f.unit)
            f.override_reason = str(r.choice(OVERRIDE_REASONS_UP))
            return f.override_qty
        if roll < 0.16:
            f.status = "overridden"
            f.override_qty = self._round(f.recommended_qty * r.uniform(0.84, 0.93), f.unit)
            f.override_reason = str(r.choice(OVERRIDE_REASONS_DOWN))
            return f.override_qty
        f.status = "approved"
        return f.recommended_qty

    @staticmethod
    def _round(v: float, unit: str) -> float:
        return math.ceil(v * 2) / 2 if unit in ("kg", "litres") else float(math.ceil(v))

    # ------------------------------------------------------------------ surplus history

    def _incidents_history(self) -> None:
        """Past rescues with their donations, handovers and impact (for impact reporting)."""
        fest_days = [e.day for e in self.world.events if e.kind == "fest" and e.day < self.today]
        fest_ago = (self.today - max(fest_days)).days if fest_days else 4
        # days ago, canteen, meal, trigger, food, [(place, share of portions, minutes to agree)]
        plans = [
            (62, "north_mess", "dinner", "end_of_service", [("rice", 18.0), ("dal_tadka", 14.0), ("chapati", 160)], [("annapurna", 1.0, 9)]),
            (48, "central_canteen", "lunch", "disruption", [("fried_rice", 70), ("rajma", 55)], [("roti", 1.0, 14)]),
            (41, "north_mess", "lunch", "manual", [("veg_biryani", 95), ("curd", 6.0)], [("sahara", 1.0, 11)]),
            (33, "north_mess", "dinner", "end_of_service", [("rice", 15.0), ("chole", 70)], [("asha", 1.0, 7)]),
            (26, "central_canteen", "snacks", "end_of_service", [("samosa", 120), ("vada_pav", 80)], [("annapurna", 1.0, 12)]),
            (19, "north_mess", "dinner", "manual", [("paneer_bm", 60), ("chapati", 220)], [("stars", 0.6, 16), ("sahara", 0.4, 22)]),
            (fest_ago, "north_mess", "dinner", "disruption", [("rice", 30.0), ("dal_tadka", 22.0), ("chole", 140)],
             [("annapurna", 0.7, 8), ("roti", 0.3, 15)]),
            (12, "central_canteen", "lunch", "end_of_service", [("chole_bhature", 45)], [("goshala", 1.0, 30)]),
            (5, "north_mess", "lunch", "end_of_service", [("aloo_gobi", 75), ("rice", 12.0)], [("roti", 1.0, 19)]),
        ]
        notes = {
            "end_of_service": "Unserved quantity above threshold at end of service",
            "disruption": "Diners stayed away after the food was cooked",
            "manual": "Reported by the kitchen",
        }
        with system_session() as db:
            for days_ago, ck, meal, trigger, items, givers in plans:
                c = next(x for x in CANTEENS if x.key == ck)
                day = self.today - timedelta(days=days_ago)
                meal_def = next(m for m in c.meals if m["key"] == meal)
                cooked_at = at_local(day, meal_def["start"], TZ) - timedelta(minutes=60)
                created = at_local(day, meal_def["end"], TZ) - timedelta(minutes=10)
                item_rows, portions, kg = [], 0.0, 0.0
                for dk, qty in items:
                    d = DISH_BY_KEY[dk]
                    p = qty if d.unit not in ("kg", "litres") else qty / 0.2
                    portions += p if d.unit != "pieces" else qty / 2.5
                    kg += qty * d.portion_kg
                    item_rows.append({"dish_id": str(self.dish_ids[dk]), "name": d.name, "qty": qty, "unit": d.unit,
                                      "kg": round(qty * d.portion_kg, 1), "cooked_at": cooked_at.isoformat(),
                                      "storage": "hot_held", "diet": d.diet, "food_category": d.category,
                                      "allergens": list(d.allergens), "cost": round(d.cost * qty)})
                divert = all(DEMO_PLACES[g][1] == "animal_shelter" for g, _, _ in givers)
                inc = SurplusIncident(
                    tenant_id=self.tenant_id, canteen_id=self.canteen_ids[ck], status="closed", trigger=trigger,
                    items=[{**i, "route": "divert" if divert else "people"} for i in item_rows],
                    total_portions=round(portions), total_kg=round(kg, 1), safe_until=cooked_at + timedelta(hours=4),
                    route="divert" if divert else "people", pickup_point=c.pickup_point,
                    notes="Past the safe window by the time it was reported" if divert else notes[trigger],
                    threshold={"min_portions": 20, "required_kg": 5.0, "meets": True}, created_by=self.org_user,
                    created_at=created, closed_at=created + timedelta(minutes=95),
                    first_agreed_at=created + timedelta(minutes=min(m for _, _, m in givers)),
                )
                db.add(inc)
                db.flush()
                events = [(0, "detected", f"{round(portions)} portions ({kg:.1f} kg) reported")]
                events.append((1, "safety", "Not safe to give to people: past the safe window" if divert
                               else "Safety check passed: safe for about 2 h 50 min"))
                given_kg, names = 0.0, []
                for key, share, minutes in givers:
                    name, kind, lat, lon = DEMO_PLACES[key]
                    weight = round(kg * share * 0.97, 1)
                    given_kg += weight
                    names.append(name)
                    db.add(Donation(
                        tenant_id=self.tenant_id, incident_id=inc.id, place_ref=f"demo:{key}", place_name=name,
                        place_kind=kind, place_lat=lat, place_lon=lon, distance_km=round(1.5 + minutes / 10, 1),
                        portions=round(portions * share), weight_kg=weight, status="handed_over",
                        contact_method="call" if minutes < 15 else "whatsapp", created_by=self.org_user,
                        handed_over_at=created + timedelta(minutes=minutes + 30), created_at=created + timedelta(minutes=minutes),
                    ))
                    events.append((minutes, "agreed", f"{name} agreed to take {portions * share:.0f} portions"))
                    events.append((minutes + 30, "handed_over", f"Handed {weight:.1f} kg to {name}"))
                meals = 0 if divert else round(given_kg / 0.4)
                events.append((95, "closed", f"All food handed over. {given_kg:.1f} kg handed over"
                               + ("" if divert else f", about {meals} meals") + "."))
                inc.impact = {"kg": round(given_kg, 1), "meals": meals, "co2e_kg": round(given_kg * 2.5, 1),
                              "rupees": 0 if divert else round(sum(DISH_BY_KEY[dk].cost * q for dk, q in items) * 0.97),
                              "minutes_to_agree": float(min(m for _, _, m in givers)),
                              "route": "divert" if divert else "people", "disposed_kg": round(kg - given_kg, 1),
                              "disposal": None, "places": names}
                for minutes, kind, message in events:
                    db.add(IncidentEvent(tenant_id=self.tenant_id, incident_id=inc.id, kind=kind, message=message,
                                         actor="system" if kind in ("detected", "safety", "closed") else ORG_NAME,
                                         created_at=created + timedelta(minutes=minutes)))
            db.commit()

    def _queue_pictures(self) -> None:
        """Every dish gets a picture from Wikimedia Commons once a worker picks these up."""
        with system_session() as db:
            for dish_id, tenant_id in db.execute(select(Dish.id, Dish.tenant_id)).all():
                jobs.enqueue(db, "find_dish_image", {"dish_id": str(dish_id)}, tenant_id=tenant_id,
                             dedupe_key=f"dish-image:{dish_id}")
            db.commit()

    # ------------------------------------------------------------------ a second, brand-new college

    def _riverside(self) -> None:
        """Riverside College signed up ten days ago: Tier 0, rule-based plans, readiness meter."""
        rc = CanteenDef(
            key="riverside", name="Riverside Canteen", kind="canteen", service_mode="counter", lat=18.5590, lon=73.7868,
            diners=380, pickup_point="Canteen back gate", meals=(
                {"key": "lunch", "label": "Lunch", "start": "12:30", "end": "15:00"},
                {"key": "snacks", "label": "Evening snacks", "start": "16:30", "end": "18:30"},
            ),
            base_rate={"lunch": 0.6, "snacks": 0.45},
            menu={d: {"lunch": ["fried_rice", "rajma", "rice"], "snacks": ["samosa", "chai", "vada_pav"]} for d in range(7)},
            weekday={(6, "lunch"): 0.3, (6, "snacks"): 0.3}, choice=True, noise=0.07,
        )
        w = World(self.today, seed=77)
        with system_session() as db:
            tenant = Tenant(name="Riverside College of Commerce", slug="riverside", timezone=TZ, service_mode="counter",
                            is_demo=True, settings={})
            db.add(tenant)
            db.flush()
            campus = Campus(tenant_id=tenant.id, name="Aundh campus", lat=18.5590, lon=73.7868)
            db.add(campus)
            db.flush()
            canteen = Canteen(tenant_id=tenant.id, campus_id=campus.id, name=rc.name, kind="canteen",
                              service_mode="counter", lat=rc.lat, lon=rc.lon, pickup_point=rc.pickup_point,
                              meals=[dict(m) for m in rc.meals], expected_diners=rc.diners)
            db.add(canteen)
            db.flush()
            dish_ids = {}
            for dk in ["fried_rice", "rajma", "rice", "samosa", "chai", "vada_pav"]:
                d = DISH_BY_KEY[dk]
                dish = Dish(tenant_id=tenant.id, name=d.name, name_hi=d.name_hi, course=d.course, diet=d.diet,
                            unit=d.unit, portion_kg=d.portion_kg, cost_per_unit=d.cost, food_category=d.category,
                            allergens=list(d.allergens), aliases=list(d.aliases))
                db.add(dish)
                db.flush()
                dish_ids[dk] = dish.id
            db.add(User(tenant_id=tenant.id, email="team@riverside.example", name="Riverside canteen team",
                        role="organization", password_hash=security.hash_password(DEMO_PASSWORD), is_seed=True))
            rows, menus = [], []
            for offset in range(10, -2, -1):
                day = self.today - timedelta(days=offset)
                for meal, dishes in rc.menu[day.weekday()].items():
                    for dk in dishes:
                        menus.append({"tenant_id": tenant.id, "canteen_id": canteen.id, "service_date": day, "meal": meal,
                                      "dish_id": dish_ids[dk]})
                    if offset <= 0:
                        continue
                    ff = w.footfall(rc, day, meal)
                    for dk in dishes:
                        demand = w.demand(rc, day, meal, dk, ff, dishes)
                        prepared = math.ceil(demand * 1.18) if DISH_BY_KEY[dk].unit != "kg" else round(demand * 1.18, 1)
                        rows.append({"tenant_id": tenant.id, "canteen_id": canteen.id, "service_date": day, "meal": meal,
                                     "dish_id": dish_ids[dk], "unit": DISH_BY_KEY[dk].unit, "prepared_qty": prepared,
                                     "served_qty": demand, "unserved_qty": round(prepared - demand, 1),
                                     "plate_waste_qty": round(demand * 0.05, 1), "spoilage_qty": 0.0, "footfall": ff,
                                     "sold_out": False, "source": "kitchen", "quality_flags": []})
            _bulk(db, MenuEntry, menus)
            _bulk(db, ServiceLog, rows)
            db.commit()
            riverside_id, canteen_id = tenant.id, canteen.id
        training.train_canteen(riverside_id, canteen_id, trigger="onboarding")
        with tenant_session(riverside_id) as db:
            tenant = db.get(Tenant, riverside_id)
            canteen = db.get(Canteen, canteen_id)
            for day in (self.today, self.today + timedelta(days=1)):
                serving.generate(db, tenant, canteen, day)
            db.commit()


def is_empty() -> bool:
    with system_session() as db:
        return db.execute(select(Tenant.id).limit(1)).first() is None


def reset_all() -> None:
    if get_settings().env == "production":
        raise RuntimeError("Refusing to reset data in production.")
    with system_session() as db:
        db.execute(text("ALTER TABLE audit_logs DISABLE TRIGGER audit_logs_no_update"))
        db.execute(delete(AuditLog))
        db.execute(text("ALTER TABLE audit_logs ENABLE TRIGGER audit_logs_no_update"))
        db.execute(text(
            "TRUNCATE tenants, refresh_tokens, users, jobs, outbox_messages, place_cache, worker_heartbeats "
            "RESTART IDENTITY CASCADE"
        ))
        db.commit()


def seed_demo(reset: bool = False) -> dict:
    if reset:
        reset_all()
    return Seeder().run()
