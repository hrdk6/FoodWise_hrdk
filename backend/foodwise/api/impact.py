"""Impact: waste avoided (measured on the college's own logs), food rescued, money and CO2e, and exports.

Honesty rules from the design: the waste-reduction figure compares days cooked to a FoodWise plan
against days cooked by hand, on this college's own logs, with a bootstrap interval. It is shown
alongside how often dishes ran out in each period, because cutting waste by starving students is
not a win.
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import date, timedelta
from statistics import median

import numpy as np
from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import and_, func, select

from foodwise.core import audit
from foodwise.core.deps import Ctx, need
from foodwise.core.errors import Unprocessable
from foodwise.core.timeutil import local_today
from foodwise.db.models import Canteen, Dish, Forecast, ServiceLog, SurplusIncident, Tenant
from foodwise.domain.settings import effective_settings

router = APIRouter(tags=["impact"])


class Period(BaseModel):
    days: int
    prepared_kg: float
    unserved_kg: float
    waste_pct: float | None
    sold_out_pct: float | None


class Reduction(BaseModel):
    point: float | None
    lo: float | None
    hi: float | None
    note: str


class Rescue(BaseModel):
    incidents: int
    above_threshold: int
    rescued: int
    rescue_rate: float | None
    kg: float
    meals: int
    median_minutes_to_agree: float | None
    diverted_kg: float


class ImpactSummary(BaseModel):
    start: date
    end: date
    manual: Period
    guided: Period
    reduction: Reduction
    avoided_kg: float
    rupees_saved: float
    rescue: Rescue
    co2e_kg: float
    co2e_note: str
    meals_donated: int
    weekly: list[dict]
    benchmark: str


def _daily(ctx: Ctx, start: date, end: date, canteen_id: uuid.UUID | None):
    kg = Dish.portion_kg
    guided = (
        select(Forecast.canteen_id, Forecast.service_date).where(Forecast.status.in_(["approved", "overridden"]),
                                                                 Forecast.kind != "shadow")
        .distinct().subquery()
    )
    q = (
        select(ServiceLog.canteen_id, ServiceLog.service_date, func.sum(ServiceLog.prepared_qty * kg),
               func.sum(ServiceLog.unserved_qty * kg), func.count().filter(ServiceLog.sold_out), func.count(),
               func.sum(ServiceLog.unserved_qty * Dish.cost_per_unit), func.count(guided.c.service_date))
        .join(Dish, Dish.id == ServiceLog.dish_id)
        .outerjoin(guided, and_(guided.c.canteen_id == ServiceLog.canteen_id, guided.c.service_date == ServiceLog.service_date))
        .where(ServiceLog.service_date >= start, ServiceLog.service_date <= end)
        .group_by(ServiceLog.canteen_id, ServiceLog.service_date)
        .order_by(ServiceLog.service_date)
    )
    if canteen_id:
        ctx.check_canteen(canteen_id)
        q = q.where(ServiceLog.canteen_id == canteen_id)
    elif ctx.principal.canteen_ids:
        q = q.where(ServiceLog.canteen_id.in_(ctx.principal.canteen_ids))
    return [dict(canteen=c, day=d, prepared=float(p or 0), unserved=float(u or 0), sold=int(s or 0), n=int(n or 0),
                 rupees=float(r or 0), guided=bool(g)) for c, d, p, u, s, n, r, g in ctx.db.execute(q)]


def _period(rows: list[dict]) -> Period:
    prepared = sum(r["prepared"] for r in rows)
    unserved = sum(r["unserved"] for r in rows)
    n = sum(r["n"] for r in rows)
    return Period(days=len({r["day"] for r in rows}), prepared_kg=round(prepared, 1), unserved_kg=round(unserved, 1),
                  waste_pct=round(unserved / prepared, 4) if prepared else None,
                  sold_out_pct=round(sum(r["sold"] for r in rows) / n, 4) if n else None)


def _bootstrap(manual: list[dict], guided: list[dict], n: int = 600) -> Reduction:
    if len(manual) < 7 or len(guided) < 7:
        return Reduction(point=None, lo=None, hi=None,
                         note="Needs at least a week of hand-planned days and a week of FoodWise-planned days.")
    rng = np.random.default_rng(11)
    m = np.array([[r["unserved"], r["prepared"]] for r in manual])
    g = np.array([[r["unserved"], r["prepared"]] for r in guided])
    rate = lambda a: a[:, 0].sum() / a[:, 1].sum()  # noqa: E731
    point = 1 - rate(g) / rate(m)
    samples = []
    for _ in range(n):
        ms = m[rng.integers(0, len(m), len(m))]
        gs = g[rng.integers(0, len(g), len(g))]
        if rate(ms) > 0:
            samples.append(1 - rate(gs) / rate(ms))
    lo, hi = np.percentile(samples, [5, 95])
    return Reduction(point=round(float(point), 4), lo=round(float(lo), 4), hi=round(float(hi), 4),
                     note=(f"Unserved food as a share of food cooked: {len(guided)} canteen-days cooked to a FoodWise plan "
                           f"vs {len(manual)} cooked by hand, with a 90% bootstrap interval."))


@router.get("/impact/summary", response_model=ImpactSummary)
def summary(days: int = Query(90, ge=14, le=365), canteen_id: uuid.UUID | None = None,
            ctx: Ctx = Depends(need("impact:read"))) -> ImpactSummary:
    tenant = ctx.db.get(Tenant, ctx.tenant_id)
    cfg = effective_settings(tenant.settings)["impact"]
    end = local_today(tenant.timezone) - timedelta(days=1)
    start = end - timedelta(days=days)
    rows = _daily(ctx, start, end, canteen_id)
    manual = [r for r in rows if not r["guided"]]
    guided = [r for r in rows if r["guided"]]
    pm, pg = _period(manual), _period(guided)
    reduction = _bootstrap(manual, guided)
    avoided = 0.0
    rupees = 0.0
    if pm.waste_pct is not None and pg.waste_pct is not None:
        avoided = max(0.0, (pm.waste_pct - pg.waste_pct) * pg.prepared_kg)
        cost_per_kg = (sum(r["rupees"] for r in manual) / pm.unserved_kg) if pm.unserved_kg else 0
        rupees = avoided * cost_per_kg

    q = select(SurplusIncident).where(func.date(SurplusIncident.created_at) >= start)
    if canteen_id:
        q = q.where(SurplusIncident.canteen_id == canteen_id)
    incidents = ctx.db.execute(q).scalars().all()
    above = [i for i in incidents if (i.threshold or {}).get("meets", True) and i.status not in ("rejected", "cancelled")]
    rescued = [i for i in above if i.status == "closed" and (i.impact or {}).get("route") == "people"
               and (i.impact or {}).get("kg", 0) > 0]
    kg_rescued = sum((i.impact or {}).get("kg", 0) for i in rescued)
    meals = sum((i.impact or {}).get("meals", 0) for i in rescued)
    minutes = [m for i in rescued if (m := (i.impact or {}).get("minutes_to_agree", (i.impact or {}).get("rescue_minutes")))]
    diverted = sum((i.impact or {}).get("kg", 0) for i in above if (i.impact or {}).get("route") in ("compost", "divert"))
    co2e = (avoided + kg_rescued) * cfg["co2e_kg_per_kg_food"]

    weekly: dict[date, dict] = {}
    for r in rows:
        wk = r["day"] - timedelta(days=r["day"].weekday())
        w = weekly.setdefault(wk, {"week": wk.isoformat(), "prepared_kg": 0.0, "unserved_kg": 0.0, "guided_days": 0, "days": 0})
        w["prepared_kg"] += r["prepared"]
        w["unserved_kg"] += r["unserved"]
        w["days"] += 1
        w["guided_days"] += int(r["guided"])
    for w in weekly.values():
        w["waste_pct"] = round(w["unserved_kg"] / w["prepared_kg"], 4) if w["prepared_kg"] else None
        w["prepared_kg"], w["unserved_kg"] = round(w["prepared_kg"], 1), round(w["unserved_kg"], 1)

    bench = ("Benchmarks appear when this college and at least two others share anonymised data."
             if tenant.data_sharing != "anonymized_pool" else "Not enough colleges share data yet for a fair comparison.")
    return ImpactSummary(
        start=start, end=end, manual=pm, guided=pg, reduction=reduction, avoided_kg=round(avoided, 1),
        rupees_saved=round(rupees), co2e_kg=round(co2e, 1), co2e_note=cfg["co2e_source"], meals_donated=int(meals),
        rescue=Rescue(incidents=len(incidents), above_threshold=len(above), rescued=len(rescued),
                      rescue_rate=round(len(rescued) / len(above), 3) if above else None, kg=round(kg_rescued, 1),
                      meals=int(meals), median_minutes_to_agree=round(median(minutes), 1) if minutes else None,
                      diverted_kg=round(diverted, 1)),
        weekly=[weekly[k] for k in sorted(weekly)], benchmark=bench,
    )


@router.get("/reports/logs.csv")
def logs_csv(start: date, end: date, canteen_id: uuid.UUID | None = None, ctx: Ctx = Depends(need("reports:export"))):
    if (end - start).days > 400:
        raise Unprocessable("Export at most about a year at a time.")
    q = (select(ServiceLog, Dish.name, Canteen.name).join(Dish, Dish.id == ServiceLog.dish_id)
         .join(Canteen, Canteen.id == ServiceLog.canteen_id)
         .where(ServiceLog.service_date >= start, ServiceLog.service_date <= end)
         .order_by(ServiceLog.service_date, Canteen.name, ServiceLog.meal, Dish.name))
    if canteen_id:
        ctx.check_canteen(canteen_id)
        q = q.where(ServiceLog.canteen_id == canteen_id)
    audit.record(ctx.db, ctx.principal, "report.exported", meta={"report": "logs.csv", "start": str(start), "end": str(end)})
    ctx.db.commit()

    def gen():
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["date", "canteen", "meal", "dish", "unit", "prepared_qty", "served_qty", "leftover_qty",
                    "plate_waste_qty", "spoilage_qty", "footfall", "sold_out", "source"])
        yield buf.getvalue()
        for log, dish, canteen in ctx.db.execute(q):
            buf.seek(0)
            buf.truncate()
            w.writerow([log.service_date, canteen, log.meal, dish, log.unit, log.prepared_qty, log.served_qty,
                        log.unserved_qty, log.plate_waste_qty, log.spoilage_qty, log.footfall or "", log.sold_out, log.source])
            yield buf.getvalue()

    return StreamingResponse(gen(), media_type="text/csv",
                             headers={"Content-Disposition": f"attachment; filename=foodwise_logs_{start}_{end}.csv"})


@router.get("/reports/impact.pdf")
def impact_pdf(days: int = Query(90, ge=14, le=365), ctx: Ctx = Depends(need("reports:export"))) -> Response:
    from fpdf import FPDF

    s = summary(days, None, ctx)
    tenant = ctx.db.get(Tenant, ctx.tenant_id)
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(True, 18)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 20)
    pdf.cell(0, 12, "FoodWise impact report", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 11)
    pdf.cell(0, 7, f"{tenant.name}   {s.start:%d %b %Y} to {s.end:%d %b %Y}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    def row(label: str, value: str) -> None:
        pdf.set_font("Helvetica", "", 11)
        pdf.cell(110, 8, label)
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 8, value, new_x="LMARGIN", new_y="NEXT")

    def pct(v):
        return "n/a" if v is None else f"{v * 100:.1f}%"

    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 9, "Cooking to plan", new_x="LMARGIN", new_y="NEXT")
    row("Unserved food, days cooked by hand", f"{pct(s.manual.waste_pct)} of {s.manual.prepared_kg:,.0f} kg cooked")
    row("Unserved food, days cooked to a FoodWise plan", f"{pct(s.guided.waste_pct)} of {s.guided.prepared_kg:,.0f} kg cooked")
    if s.reduction.point is not None:
        row("Waste reduction (90% interval)", f"{pct(s.reduction.point)} ({pct(s.reduction.lo)} to {pct(s.reduction.hi)})")
    row("Dishes that ran out, by hand / with FoodWise", f"{pct(s.manual.sold_out_pct)} / {pct(s.guided.sold_out_pct)}")
    row("Food not cooked in the first place", f"{s.avoided_kg:,.0f} kg")
    row("Ingredient cost avoided (estimate)", f"Rs {s.rupees_saved:,.0f}")
    pdf.ln(3)
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 9, "Surplus rescue", new_x="LMARGIN", new_y="NEXT")
    row("Large surpluses", str(s.rescue.above_threshold))
    row("Handed over to people", f"{s.rescue.rescued} ({pct(s.rescue.rescue_rate)})")
    row("Food rescued", f"{s.rescue.kg:,.0f} kg, about {s.rescue.meals:,} meals")
    if s.rescue.median_minutes_to_agree is not None:
        row("Median time to find a place", f"{s.rescue.median_minutes_to_agree:.0f} minutes")
    row("Estimated emissions avoided", f"{s.co2e_kg:,.0f} kg CO2e")
    pdf.ln(4)
    pdf.set_font("Helvetica", "I", 9)
    pdf.multi_cell(0, 5, f"Method: {s.reduction.note} Emission factor: {s.co2e_note} FoodWise records and coordinates "
                         "donations; it does not replace the college's legal and food-safety obligations.")
    audit.record(ctx.db, ctx.principal, "report.exported", meta={"report": "impact.pdf", "days": days})
    ctx.db.commit()
    return Response(bytes(pdf.output()), media_type="application/pdf",
                    headers={"Content-Disposition": f"attachment; filename=foodwise_impact_{s.end}.pdf"})
