"""A synthetic college with known ground truth (the "digital twin").

True demand is generated from realistic causes: the semester calendar, Pune weather, exams,
fests, weekends, month-end budgets, RSVPs, and dish quality that changes over time. The seed
script runs FoodWise against this world, and the ML regression tests use it as a golden dataset:
a model that cannot recover the weekday, rain and exam effects planted here fails CI.

Everything is deterministic for a given seed: each random draw is keyed by what it describes
(canteen, day, meal, dish), not by call order.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np

MONTH_TEMP = [21, 23, 27, 30, 31, 27.5, 25, 24.5, 25, 25.5, 23, 21]
MONTH_RAIN = [0.03, 0.03, 0.05, 0.08, 0.15, 0.55, 0.75, 0.7, 0.55, 0.3, 0.1, 0.05]


def _seed(*keys) -> int:
    return int(hashlib.md5("|".join(str(k) for k in keys).encode()).hexdigest()[:15], 16)


def rng(*keys) -> np.random.Generator:
    return np.random.default_rng(_seed(*keys))


def stable_int(*keys) -> int:
    """A process-independent hash (Python's hash() is salted per process)."""
    return _seed(*keys)


@dataclass(frozen=True)
class DishDef:
    key: str
    name: str
    name_hi: str
    course: str
    diet: str
    cuisine: str
    unit: str
    portion_kg: float
    cost: float
    category: str
    allergens: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    quality: float = 0.0
    weakness: tuple[str, ...] = ("taste",)


DISHES: list[DishDef] = [
    DishDef("poha", "Kanda poha", "कांदा पोहा", "breakfast", "veg", "maharashtrian", "plates", 0.2, 18, "cooked_rice", (), ("Poha",), 0.35),
    DishDef("upma", "Upma", "उपमा", "breakfast", "veg", "south_indian", "plates", 0.2, 16, "cooked_rice", ("gluten",), (), -0.2, ("taste", "temperature")),
    DishDef("idli", "Idli sambar", "इडली सांबर", "breakfast", "veg", "south_indian", "plates", 0.25, 22, "curry_gravy", (), ("Idli",), 0.2, ("temperature",)),
    DishDef("aloo_paratha", "Aloo paratha", "आलू पराठा", "breakfast", "veg", "punjabi", "pieces", 0.12, 15, "bread", ("gluten",), ("Alu paratha",), 0.55, ("quantity",)),
    DishDef("masala_dosa", "Masala dosa", "मसाला डोसा", "breakfast", "veg", "south_indian", "plates", 0.25, 40, "bread", (), ("Dosa",), 0.45),
    DishDef("bread_omelette", "Bread omelette", "ब्रेड ऑमलेट", "breakfast", "egg", "street", "plates", 0.2, 30, "fried_snack", ("gluten", "egg"), (), 0.3),
    DishDef("chai", "Masala chai", "मसाला चाय", "beverage", "veg", "indian", "pieces", 0.15, 10, "beverage", ("dairy",), ("Tea", "Chai"), 0.4),
    DishDef("rice", "Steamed rice", "चावल", "rice", "veg", "indian", "kg", 1.0, 60, "cooked_rice", (), ("Rice", "Plain rice"), 0.1),
    DishDef("jeera_rice", "Jeera rice", "जीरा राइस", "rice", "veg", "north_indian", "kg", 1.0, 75, "cooked_rice", (), (), 0.3),
    DishDef("dal_tadka", "Dal tadka", "दाल तड़का", "dal", "veg", "north_indian", "litres", 1.0, 70, "dal", (), ("Dal", "Yellow dal"), 0.0, ("temperature", "taste")),
    DishDef("dal_makhani", "Dal makhani", "दाल मखनी", "dal", "veg", "punjabi", "litres", 1.0, 110, "dal", ("dairy",), (), 0.5),
    DishDef("chapati", "Chapati", "चपाती", "bread", "veg", "indian", "pieces", 0.04, 4, "bread", ("gluten",), ("Roti", "Chapatti"), 0.15, ("temperature", "freshness")),
    DishDef("aloo_gobi", "Aloo gobi", "आलू गोभी", "sabzi", "veg", "north_indian", "plates", 0.15, 20, "curry_gravy", (), ("Alu gobi",), 0.2, ("taste", "freshness")),
    DishDef("bhindi", "Bhindi masala", "भिंडी मसाला", "sabzi", "veg", "north_indian", "plates", 0.13, 22, "curry_gravy", (), ("Bhindi",), 0.05, ("taste",)),
    DishDef("mix_veg", "Mixed veg", "मिक्स वेज", "sabzi", "veg", "north_indian", "plates", 0.15, 20, "curry_gravy", (), ("Mix veg", "Mixed vegetable"), -0.25, ("taste", "variety")),
    DishDef("rajma", "Rajma", "राजमा", "curry", "veg", "punjabi", "plates", 0.2, 28, "curry_gravy", (), ("Rajma masala",), 0.45),
    DishDef("chole", "Chole", "छोले", "curry", "veg", "punjabi", "plates", 0.2, 26, "curry_gravy", (), ("Chana masala",), 0.3),
    DishDef("paneer_bm", "Paneer butter masala", "पनीर बटर मसाला", "curry", "veg", "punjabi", "plates", 0.18, 55, "curry_gravy", ("dairy",), ("Paneer makhani", "PBM"), 0.75, ("quantity",)),
    DishDef("chicken_curry", "Chicken curry", "चिकन करी", "curry", "non_veg", "north_indian", "plates", 0.22, 70, "curry_gravy", (), ("Chicken gravy",), 0.5, ("quantity",)),
    DishDef("egg_curry", "Egg curry", "अंडा करी", "curry", "egg", "north_indian", "plates", 0.2, 35, "curry_gravy", ("egg",), (), 0.1),
    DishDef("veg_biryani", "Veg biryani", "वेज बिरयानी", "main", "veg", "hyderabadi", "plates", 0.3, 50, "cooked_rice", (), ("Veg biriyani",), 0.6),
    DishDef("chicken_biryani", "Chicken biryani", "चिकन बिरयानी", "main", "non_veg", "hyderabadi", "plates", 0.35, 90, "cooked_rice", (), (), 0.8, ("quantity",)),
    DishDef("curd", "Curd", "दही", "side", "veg", "indian", "kg", 1.0, 60, "dessert_dairy", ("dairy",), ("Dahi",), 0.2),
    DishDef("gulab_jamun", "Gulab jamun", "गुलाब जामुन", "dessert", "veg", "indian", "pieces", 0.05, 10, "dessert_dairy", ("dairy",), (), 0.7),
    DishDef("kheer", "Rice kheer", "खीर", "dessert", "veg", "indian", "plates", 0.15, 20, "dessert_dairy", ("dairy",), ("Kheer",), 0.4),
    DishDef("samosa", "Samosa", "समोसा", "snack", "veg", "street", "pieces", 0.08, 15, "fried_snack", ("gluten",), (), 0.45),
    DishDef("vada_pav", "Vada pav", "वड़ा पाव", "snack", "veg", "maharashtrian", "pieces", 0.12, 18, "fried_snack", ("gluten",), ("Wada pav",), 0.55),
    DishDef("pav_bhaji", "Pav bhaji", "पाव भाजी", "main", "veg", "maharashtrian", "plates", 0.3, 60, "curry_gravy", ("gluten", "dairy"), (), 0.6),
    DishDef("chole_bhature", "Chole bhature", "छोले भटूरे", "main", "veg", "punjabi", "plates", 0.35, 60, "fried_snack", ("gluten",), (), 0.5),
    DishDef("veg_sandwich", "Veg sandwich", "वेज सैंडविच", "snack", "veg", "street", "plates", 0.18, 35, "bread", ("gluten", "dairy"), ("Sandwich",), 0.1),
    DishDef("fried_rice", "Veg fried rice", "वेज फ्राइड राइस", "main", "veg", "indo_chinese", "plates", 0.3, 55, "cooked_rice", ("soy",), ("Fried rice",), 0.25),
    DishDef("salad", "Green salad", "सलाद", "side", "veg", "indian", "plates", 0.08, 10, "salad_raw", (), ("Salad",), -0.1),
]
DISH_BY_KEY = {d.key: d for d in DISHES}

# Per-diner take rates in each dish's own unit (pieces of chapati, kg of rice, litres of dal...).
TAKE = {
    "rice": 0.17, "jeera_rice": 0.17, "dal_tadka": 0.16, "dal_makhani": 0.15, "chapati": 2.5, "salad": 0.55,
    "curd": 0.06, "chai": 0.8, "gulab_jamun": 1.6, "kheer": 0.85,
}


@dataclass(frozen=True)
class CanteenDef:
    key: str
    name: str
    kind: str
    service_mode: str
    lat: float
    lon: float
    diners: int
    pickup_point: str
    meals: tuple[dict, ...]
    base_rate: dict
    menu: dict  # weekday (0=Mon) -> meal -> [dish keys]
    rain: dict = field(default_factory=dict)
    exam: dict = field(default_factory=dict)
    weekday: dict = field(default_factory=dict)  # (dow, meal) -> factor
    vacation: float = 0.2
    holiday: float = 0.8
    fest: dict = field(default_factory=dict)
    noise: float = 0.045
    choice: bool = False


_MESS_LUNCH = ["rice", "dal_tadka", "chapati", "salad"]
_MESS_DINNER = ["rice", "chapati"]
NORTH_MESS = CanteenDef(
    key="north_mess",
    name="North Mess",
    kind="mess",
    service_mode="fixed_thali",
    lat=18.5196,
    lon=73.8553,
    diners=1150,
    pickup_point="Loading bay behind North Mess, gate 3",
    meals=(
        {"key": "breakfast", "label": "Breakfast", "start": "07:30", "end": "09:30"},
        {"key": "lunch", "label": "Lunch", "start": "12:30", "end": "14:30"},
        {"key": "dinner", "label": "Dinner", "start": "19:30", "end": "21:30"},
    ),
    base_rate={"breakfast": 0.57, "lunch": 0.71, "dinner": 0.83},
    menu={
        0: {"breakfast": ["poha", "chai"], "lunch": _MESS_LUNCH + ["aloo_gobi"], "dinner": _MESS_DINNER + ["dal_makhani", "paneer_bm"]},
        1: {"breakfast": ["upma", "chai"], "lunch": _MESS_LUNCH + ["bhindi"], "dinner": _MESS_DINNER + ["dal_tadka", "egg_curry"]},
        2: {"breakfast": ["idli", "chai"], "lunch": _MESS_LUNCH + ["rajma"], "dinner": _MESS_DINNER + ["dal_tadka", "mix_veg", "kheer"]},
        3: {"breakfast": ["aloo_paratha", "curd", "chai"], "lunch": _MESS_LUNCH + ["mix_veg"], "dinner": _MESS_DINNER + ["dal_tadka", "chicken_curry", "paneer_bm"]},
        4: {"breakfast": ["poha", "chai"], "lunch": _MESS_LUNCH + ["chole"], "dinner": _MESS_DINNER + ["dal_tadka", "rajma"]},
        5: {"breakfast": ["bread_omelette", "upma", "chai"], "lunch": _MESS_LUNCH + ["aloo_gobi"], "dinner": _MESS_DINNER + ["dal_tadka", "chole", "gulab_jamun"]},
        6: {"breakfast": ["aloo_paratha", "curd", "chai"], "lunch": ["veg_biryani", "chicken_biryani", "curd", "salad", "gulab_jamun"], "dinner": _MESS_DINNER + ["dal_tadka", "mix_veg"]},
    },
    rain={"breakfast": 1.03, "lunch": 1.05, "dinner": 1.06},
    exam={"breakfast": 1.09, "lunch": 1.03, "dinner": 1.07},
    weekday={(6, "breakfast"): 0.7, (6, "lunch"): 1.04, (5, "dinner"): 0.84, (4, "dinner"): 0.9, (6, "dinner"): 0.92},
    vacation=0.22,
    holiday=0.8,
    fest={"breakfast": 1.0, "lunch": 0.88, "dinner": 0.76},
    noise=0.04,
)
CENTRAL_CANTEEN = CanteenDef(
    key="central_canteen",
    name="Central Canteen",
    kind="canteen",
    service_mode="counter",
    lat=18.5214,
    lon=73.8517,
    diners=900,
    pickup_point="Side door of Central Canteen, near the library",
    meals=(
        {"key": "breakfast", "label": "Breakfast", "start": "08:00", "end": "10:30"},
        {"key": "lunch", "label": "Lunch", "start": "12:00", "end": "15:00"},
        {"key": "snacks", "label": "Evening snacks", "start": "16:30", "end": "19:00"},
    ),
    base_rate={"breakfast": 0.34, "lunch": 0.62, "snacks": 0.47},
    menu={
        dow: {
            "breakfast": ["masala_dosa", "idli", "poha", "chai"] + (["bread_omelette"] if dow < 6 else []),
            "lunch": ["fried_rice", "rajma", "rice"] + [["pav_bhaji"], ["chole_bhature"], ["veg_biryani"], ["pav_bhaji"],
                                                        ["chole_bhature"], ["veg_biryani"], ["pav_bhaji"]][dow],
            "snacks": ["samosa", "vada_pav", "chai", "veg_sandwich"],
        }
        for dow in range(7)
    },
    rain={"breakfast": 0.9, "lunch": 0.85, "snacks": 1.14},
    exam={"breakfast": 0.85, "lunch": 0.82, "snacks": 1.08},
    weekday={(5, "breakfast"): 0.5, (5, "lunch"): 0.55, (5, "snacks"): 0.5, (6, "breakfast"): 0.3, (6, "lunch"): 0.32, (6, "snacks"): 0.35},
    vacation=0.17,
    holiday=0.22,
    fest={"breakfast": 1.1, "lunch": 1.55, "snacks": 1.6},
    noise=0.06,
    choice=True,
)
CANTEENS = [NORTH_MESS, CENTRAL_CANTEEN]


@dataclass
class CalEvent:
    day: date
    kind: str
    label: str
    canteen: str | None = None
    meal: str | None = None
    attendance: int | None = None
    disruption: str | None = None


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    d = date(year, month, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


class World:
    def __init__(self, today: date, seed: int = 2026, history_days: int = 240, future_days: int = 14):
        self.seed = seed
        self.today = today
        self.start = today - timedelta(days=history_days)
        self.end = today + timedelta(days=future_days)
        self.events = self._calendar()
        self._by_day: dict[date, list[CalEvent]] = {}
        for e in self.events:
            self._by_day.setdefault(e.day, []).append(e)

    # ----------------------------------------------------------------- calendar

    def _calendar(self) -> list[CalEvent]:
        ev: list[CalEvent] = []

        def span(start: date, days: int, kind: str, label: str) -> None:
            for i in range(days):
                d = start + timedelta(days=i)
                if kind != "exam" or d.weekday() < 6:
                    ev.append(CalEvent(d, kind, label))

        for year in range(self.start.year - 1, self.end.year + 1):
            even, odd = date(year, 1, 5), date(year, 7, 20)
            ev.append(CalEvent(even, "term_start", "Even semester begins"))
            ev.append(CalEvent(odd, "term_start", "Odd semester begins"))
            span(even + timedelta(weeks=9), 6, "exam", "Mid-semester exams")
            span(even + timedelta(weeks=17), 13, "exam", "End-semester exams")
            span(even + timedelta(weeks=17, days=14), (odd - (even + timedelta(weeks=17, days=14))).days, "vacation", "Summer vacation")
            span(odd + timedelta(weeks=9), 6, "exam", "Mid-semester exams")
            span(odd + timedelta(weeks=18), 13, "exam", "End-semester exams")
            span(date(year, 12, 21), 15, "vacation", "Winter vacation")
            for m, d, label in [(1, 26, "Republic Day"), (3, 14, "Holi"), (5, 1, "Maharashtra Day"), (8, 15, "Independence Day"),
                                (10, 2, "Gandhi Jayanti"), (10, 22, "Dussehra"), (11, 9, "Diwali"), (11, 10, "Diwali")]:
                ev.append(CalEvent(date(year, m, d), "holiday", label))
            fest = _nth_weekday(year, 10, 4, 2)
            for i in range(2):
                ev.append(CalEvent(fest + timedelta(days=i), "fest", "Sahyadri cultural fest", attendance=2600))
            tech = _nth_weekday(year, 2, 4, 2)
            for i in range(2):
                ev.append(CalEvent(tech + timedelta(days=i), "fest", "Techfest", attendance=1800))

        t = self.today
        ev += [
            CalEvent(t - timedelta(days=44), "event", "Inter-college cricket final", attendance=700),
            CalEvent(t - timedelta(days=39), "event", "Placement drive", attendance=520),
            CalEvent(t - timedelta(days=38), "event", "Placement drive", attendance=480),
            CalEvent(t - timedelta(days=74), "disruption", "Power cut during dinner prep", canteen="north_mess",
                     meal="dinner", disruption="power_cut"),
            CalEvent(t - timedelta(days=17), "event", "Guest lecture series", attendance=300),
            CalEvent(t + timedelta(days=5), "event", "Alumni meet", attendance=450),
        ]
        return sorted(ev, key=lambda e: e.day)

    def day_events(self, day: date, canteen: str | None = None) -> list[CalEvent]:
        return [e for e in self._by_day.get(day, []) if e.canteen in (None, canteen)]

    def has(self, day: date, kind: str, canteen: str | None = None, meal: str | None = None) -> bool:
        return any(e.kind == kind and (meal is None or e.meal in (None, meal)) for e in self.day_events(day, canteen))

    # ----------------------------------------------------------------- weather

    def weather(self, day: date) -> tuple[float, float, bool]:
        """(temperature C, forecast rain probability, did it rain)."""
        m = day.month - 1
        r = rng(self.seed, "weather", day.toordinal())
        # A slowly varying anomaly gives realistic runs of wet and dry days.
        anomaly = 0.0
        for lag in range(4):
            anomaly += 0.45 ** lag * rng(self.seed, "wx-anom", day.toordinal() - lag).normal(0, 0.18)
        prob = float(np.clip(MONTH_RAIN[m] + anomaly, 0.0, 0.98))
        if day == self.today + timedelta(days=1):
            prob = 0.72  # the demo's tomorrow is a rainy day
        rained = bool(r.random() < prob)
        temp = MONTH_TEMP[m] + r.normal(0, 1.4) - (1.5 if rained else 0)
        return round(float(temp), 1), round(prob, 2), rained

    # ----------------------------------------------------------------- menus

    def menu(self, c: CanteenDef, day: date) -> dict[str, list[str]]:
        return {meal: list(dishes) for meal, dishes in c.menu[day.weekday()].items()}

    # ----------------------------------------------------------------- demand

    def quality(self, dish: str, day: date) -> float:
        q = DISH_BY_KEY[dish].quality
        if dish == "aloo_gobi" and day >= self.today - timedelta(days=55):
            q = -0.55  # new cook: ratings and uptake fall
        if dish == "mix_veg" and day >= self.today - timedelta(days=30):
            q = -0.1  # recipe tweak after complaints, modest recovery
        return q

    def footfall(self, c: CanteenDef, day: date, meal: str) -> int:
        r = rng(self.seed, "ff", c.key, day.toordinal(), meal)
        f = c.diners * c.base_rate[meal]
        f *= c.weekday.get((day.weekday(), meal), 1.0)
        if self.has(day, "vacation"):
            f *= c.vacation
        elif self.has(day, "holiday"):
            f *= c.holiday
        if self.has(day, "exam"):
            f *= c.exam.get(meal, 1.0)
        _, _, rained = self.weather(day)
        if rained:
            f *= c.rain.get(meal, 1.0)
        if self.has(day, "fest"):
            f *= c.fest.get(meal, 1.0)
        for e in self.day_events(day, c.key):
            if e.kind == "event" and e.attendance and c.choice and meal in ("lunch", "snacks"):
                f += e.attendance * 0.22
            if e.kind == "disruption" and e.meal in (None, meal):
                f *= 0.6
        days_to_month_end = (date(day.year + day.month // 12, day.month % 12 + 1, 1) - day).days
        if days_to_month_end <= 4 and c.kind == "mess":
            f *= 1.04
        f *= math.exp(r.normal(0, c.noise))
        return max(0, int(round(f)))

    def demand(self, c: CanteenDef, day: date, meal: str, dish: str, footfall: int, menu: list[str],
               noise: bool = True) -> float:
        d = DISH_BY_KEY[dish]
        r = rng(self.seed, "dem", c.key, day.toordinal(), meal, dish)
        q = self.quality(dish, day)
        if dish in TAKE:
            take = TAKE[dish]
        else:
            rivals = [x for x in menu if DISH_BY_KEY[x].course == d.course or
                      (DISH_BY_KEY[x].course in ("main", "curry") and d.course in ("main", "curry"))]
            if c.choice or len(rivals) > 1:
                utilities = np.array([1.3 * self.quality(x, day) + (0.25 if DISH_BY_KEY[x].diet != "veg" else 0) for x in rivals])
                share = float(np.exp(utilities[rivals.index(dish)]) / np.exp(utilities).sum())
                take = (1.1 if c.choice else 0.95) * share
            else:
                take = 0.9
            if d.course == "breakfast" and dish == "aloo_paratha":
                take *= 2.1  # pieces per diner
        take *= 1 + 0.12 * q
        _, _, rained = self.weather(day)
        if noise and rained and dish in ("samosa", "vada_pav", "chai"):
            take *= 1.12
        value = footfall * take * (math.exp(r.normal(0, 0.05)) if noise else 1.0)
        if d.unit in ("kg", "litres"):
            return round(max(0.0, value), 1)
        return float(max(0, int(round(value))))

    def regime(self, day: date) -> str:
        """What kitchen staff know about a day in advance."""
        if self.has(day, "vacation"):
            return "vacation"
        if self.has(day, "holiday"):
            return "holiday"
        return "exam" if self.has(day, "exam") else "term"

    def staff_norm(self, c: CanteenDef, day: date, meal: str, dish: str, menu: list[str]) -> float:
        """The staff's rule-of-thumb for a day: headcount norms for the known regime, no noise."""
        f = c.diners * c.base_rate[meal] * c.weekday.get((day.weekday(), meal), 1.0)
        f *= {"vacation": c.vacation, "holiday": c.holiday}.get(self.regime(day), 1.0)
        if self.regime(day) == "exam":
            f *= c.exam.get(meal, 1.0)
        return self.demand(c, day, meal, dish, int(f), menu, noise=False)

    def manual_prep(self, c: CanteenDef, day: date, meal: str, dish: str, menu: list[str],
                    last_week: float | None, last_week_sold_out: bool) -> float:
        """How the kitchen planned before FoodWise: last week's number plus a generous buffer, bumped
        after running out, and reset to headcount norms when the term regime changes."""
        r = rng(self.seed, "prep", c.key, day.toordinal(), meal, dish)
        prev_day = day - timedelta(days=7)
        if last_week is None or self.regime(prev_day) != self.regime(day):
            ref = self.staff_norm(c, day, meal, dish, menu)
        else:
            ref = last_week * (1.18 if last_week_sold_out else 1.0)
        value = ref * r.uniform(1.04, 1.2)
        d = DISH_BY_KEY[dish]
        return round(value * 2) / 2 if d.unit in ("kg", "litres") else float(math.ceil(value))

    # ----------------------------------------------------------------- students

    def rsvp(self, c: CanteenDef, day: date, meal: str, footfall: int) -> tuple[int, int]:
        """Mess residents RSVP before the cutoff. Counts track true attendance with noise."""
        if c.kind != "mess":
            return 0, 0
        r = rng(self.seed, "rsvp", c.key, day.toordinal(), meal)
        eating = int(footfall * r.uniform(0.55, 0.66))
        skipping = int(max(0, c.diners - footfall) * r.uniform(0.28, 0.4))
        return eating, skipping

    def feedback(self, c: CanteenDef, day: date, meal: str, dish: str, footfall: int) -> list[dict]:
        r = rng(self.seed, "fb", c.key, day.toordinal(), meal, dish)
        q = self.quality(dish, day)
        n = int(r.poisson(max(0.5, footfall * 0.035)))
        out = []
        d = DISH_BY_KEY[dish]
        for i in range(n):
            sat = q + r.normal(0, 0.75)
            rating = 5 if sat > 0.45 else 3 if sat > -0.35 else 1
            if r.random() < 0.3:
                rating = int(np.clip(round(3.5 + 1.3 * q + r.normal(0, 0.9)), 1, 5))
            aspects = {}
            if rating <= 2:
                for a in r.choice(list(d.weakness), size=min(len(d.weakness), 1 + int(r.random() < 0.4)), replace=False):
                    aspects[str(a)] = -1
            elif rating >= 4 and r.random() < 0.5:
                aspects["taste"] = 1
            portion = None
            if r.random() < 0.6:
                portion = "too_little" if ("quantity" in d.weakness and r.random() < 0.3) else (
                    "too_much" if r.random() < 0.06 else "right")
            text = None
            if r.random() < 0.14:
                text = _comment(r, rating, d, aspects)
            out.append({"rating": rating, "aspects": aspects, "portion": portion, "text": text, "i": i})
        return out


_COMMENTS = {
    "good": [
        "Really tasty today", "bahut accha tha", "{name} was mast", "loved it, please make it more often",
        "बहुत स्वादिष्ट था", "perfect, just like home", "ekdum badhiya", "fresh and hot, great",
    ],
    "okay": ["it was okay", "theek tha", "could be better", "average today", "thoda aur masala chahiye"],
    "taste": ["too much salt", "bilkul phika tha", "bland, no taste", "namak zyada tha", "स्वाद अच्छा नहीं था"],
    "temperature": ["{name} was cold", "thanda tha", "please serve it hot", "ठंडा था"],
    "freshness": ["tasted stale", "lagta hai baasi hai", "smells off today"],
    "quantity": ["quantity was too less", "portion kam tha", "ran out before I got some", "more please, we were still hungry"],
    "variety": ["same thing again", "every week the same, need variety", "roz roz yahi"],
}


def _comment(r: np.random.Generator, rating: int, d: DishDef, aspects: dict) -> str:
    if rating >= 4:
        pool = _COMMENTS["good"]
    elif rating == 3:
        pool = _COMMENTS["okay"]
    else:
        key = next(iter(aspects), "taste")
        pool = _COMMENTS.get(key, _COMMENTS["taste"])
    return str(r.choice(pool)).format(name=d.name)
