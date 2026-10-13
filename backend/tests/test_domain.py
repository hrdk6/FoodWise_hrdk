"""Business rules: newsvendor quantity, safe-until, nearby places, dish pictures, data quality, feedback scoring."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from foodwise.domain import places, safety
from foodwise.domain.dataquality import check_rows, match_dish, similarity
from foodwise.domain.incident_state import can_transition, transition
from foodwise.domain.newsvendor import (
    QUANTILE_LEVELS,
    critical_ratio,
    expected_waste_and_shortage,
    quantile_at,
    recommended_quantity,
    round_for_unit,
)
from foodwise.domain.preference import bayesian_score, diagnose
from foodwise.domain.readiness import readiness
from foodwise.domain.settings import DEFAULT_SETTINGS, deep_merge
from foodwise.domain.textanalysis import analyze, scrub, spam_score

GRID = [80, 85, 90, 94, 97, 100, 103, 106, 110, 115, 120]


class TestNewsvendor:
    def test_readme_example_shortage_costs_one_and_a_half_times_waste(self):
        assert critical_ratio(1.5) == pytest.approx(0.6)

    def test_never_run_out_mess_uses_p90(self):
        assert critical_ratio(9) == pytest.approx(0.9)

    def test_quantile_interpolates_on_the_grid(self):
        assert quantile_at(QUANTILE_LEVELS, GRID, 0.5) == 100
        assert quantile_at(QUANTILE_LEVELS, GRID, 0.55) == pytest.approx(101.5)

    def test_quantile_clamps_outside_the_grid(self):
        assert quantile_at(QUANTILE_LEVELS, GRID, 0.01) == 80
        assert quantile_at(QUANTILE_LEVELS, GRID, 0.999) == 120

    def test_quantile_crossing_is_repaired(self):
        crossed = list(GRID)
        crossed[5], crossed[6] = crossed[6], crossed[5]
        assert quantile_at(QUANTILE_LEVELS, crossed, 0.5) == 100

    def test_recommendation_rises_with_the_cost_of_running_short(self):
        low = recommended_quantity(QUANTILE_LEVELS, GRID, critical_ratio(1), "plates")
        high = recommended_quantity(QUANTILE_LEVELS, GRID, critical_ratio(9), "plates")
        assert low < high

    def test_rounding_by_unit(self):
        assert round_for_unit(41.2, "plates") == 42
        assert round_for_unit(41.2, "kg") == 41.5
        assert round_for_unit(-3, "plates") == 0

    def test_quantities_never_negative(self):
        assert recommended_quantity(QUANTILE_LEVELS, [0] * 11, 0.9, "kg") == 0

    def test_expected_waste_and_shortage_trade_off(self):
        w_lo, s_lo = expected_waste_and_shortage(QUANTILE_LEVELS, GRID, 90)
        w_hi, s_hi = expected_waste_and_shortage(QUANTILE_LEVELS, GRID, 115)
        assert w_hi > w_lo and s_hi < s_lo


NOW = datetime(2026, 10, 12, 21, 0, tzinfo=UTC)
WINDOWS = DEFAULT_SETTINGS["safety"]


class TestSafety:
    def test_hot_held_curry_cooked_two_hours_ago_is_safe(self):
        a = safety.assess([{"food_category": "curry_gravy", "storage": "hot_held", "cooked_at": NOW - timedelta(hours=2)}],
                          NOW, WINDOWS)
        assert a.route == "people"
        assert a.safe_until == NOW + timedelta(hours=2)

    def test_past_window_is_diverted(self):
        a = safety.assess([{"food_category": "dessert_dairy", "storage": "ambient", "cooked_at": NOW - timedelta(hours=3)}],
                          NOW, WINDOWS)
        assert a.route == "divert"
        assert any("safe window" in r for r in a.reasons)

    def test_too_little_time_left_is_diverted(self):
        a = safety.assess([{"food_category": "curry_gravy", "storage": "hot_held",
                            "cooked_at": NOW - timedelta(hours=3, minutes=30)}], NOW, WINDOWS)
        assert a.route == "divert"

    def test_unsafe_storage_state(self):
        a = safety.assess([{"food_category": "salad_raw", "storage": "hot_held", "cooked_at": NOW}], NOW, WINDOWS)
        assert a.route == "divert"

    def test_earliest_item_sets_the_incident_deadline(self):
        a = safety.assess([
            {"food_category": "cooked_rice", "storage": "hot_held", "cooked_at": NOW - timedelta(hours=1)},
            {"food_category": "bread", "storage": "hot_held", "cooked_at": NOW - timedelta(hours=1)},
        ], NOW, WINDOWS)
        assert a.safe_until == NOW + timedelta(hours=3)


def osm(tags: dict, kind: str = "node", lat: float = 18.52, lon: float = 73.85, id_: int = 1) -> dict:
    return {"type": kind, "id": id_, "lat": lat, "lon": lon, "tags": tags}


class TestPlaces:
    @pytest.mark.parametrize(("tags", "kind"), [
        ({"amenity": "social_facility", "social_facility": "soup_kitchen"}, "community_kitchen"),
        ({"amenity": "social_facility", "social_facility": "food_bank"}, "food_bank"),
        ({"amenity": "social_facility", "social_facility": "shelter", "social_facility:for": "homeless"}, "shelter"),
        ({"amenity": "social_facility", "social_facility": "group_home", "social_facility:for": "orphan"}, "children_home"),
        ({"amenity": "social_facility", "social_facility": "group_home", "social_facility:for": "senior"}, "elder_home"),
        ({"amenity": "social_facility", "social_facility": "nursing_home"}, "elder_home"),
        ({"amenity": "orphanage"}, "children_home"),
        ({"amenity": "place_of_worship", "religion": "sikh"}, "community_kitchen"),
        ({"amenity": "place_of_worship", "religion": "hindu"}, None),
        ({"office": "ngo", "name": "Helping Hands"}, "ngo"),
        ({"name": "Matoshri Vruddhashram"}, "elder_home"),
        ({"name": "Roti Bank Pune"}, "food_bank"),
        ({"amenity": "animal_shelter"}, "animal_shelter"),
        ({"name": "Shri Gaushala Trust"}, "animal_shelter"),
        ({"amenity": "recycling", "recycling:organic": "yes"}, "compost"),
        ({"amenity": "restaurant", "name": "Food Court"}, None),
        ({"amenity": "community_centre", "name": "Mangal Karyalay"}, None),
        ({"amenity": "social_facility", "social_facility": "nursing_home", "name": "Suman Nursing Home"}, None),
        ({"amenity": "social_facility", "social_facility": "nursing_home", "name": "Caring Clinic"}, None),
        ({"amenity": "social_facility", "social_facility": "group_home", "name": "Sunset Old Age Home"}, "elder_home"),
    ])
    def test_classify(self, tags, kind):
        assert places.classify(tags) == kind

    def test_unnamed_places_are_dropped_except_gurdwaras(self):
        assert places.from_osm(osm({"amenity": "orphanage"})) is None
        g = places.from_osm(osm({"amenity": "place_of_worship", "religion": "sikh"}))
        assert g is not None and g.name == "Gurdwara" and "langar" in g.note

    def test_ways_use_their_centre_and_tags_become_contact_details(self):
        p = places.from_osm({"type": "way", "id": 9, "center": {"lat": 18.5, "lon": 73.8},
                             "tags": {"amenity": "social_facility", "social_facility": "food_bank", "name": "FB",
                                      "contact:phone": "+91 1; +91 2", "addr:street": "MG Road", "addr:city": "Pune"}})
        assert p.ref == "osm:way/9" and (p.lat, p.lon) == (18.5, 73.8)
        assert p.phone == "+91 1" and p.address == "MG Road, Pune"

    @pytest.mark.parametrize(("hours", "at", "expected"), [
        ("24/7", "2026-10-13 03:00", True),
        ("Mo-Fr 09:00-17:00", "2026-10-13 10:00", True),   # a Tuesday
        ("Mo-Fr 09:00-17:00", "2026-10-17 10:00", False),  # a Saturday
        ("Mo-Sa 08:00-12:00,16:00-20:00; Su off", "2026-10-13 14:00", False),
        ("Mo-Sa 08:00-12:00,16:00-20:00; Su off", "2026-10-13 17:30", True),
        ("Mo-Sa 08:00-12:00,16:00-20:00; Su off", "2026-10-18 17:30", False),
        ("18:00-02:00", "2026-10-13 01:00", True),
        ("sunrise-sunset", "2026-10-13 10:00", None),
        (None, "2026-10-13 10:00", None),
    ])
    def test_opening_hours(self, hours, at, expected):
        assert places.is_open(hours, datetime.fromisoformat(at)) is expected

    def _place(self, name, dlat, phone="+91 1", kind="community_kitchen", hours=None):
        return places.Place(ref=f"osm:node/{name}", name=name, kind=kind, lat=18.52 + dlat, lon=73.85, phone=phone,
                            opening_hours=hours)

    def _rank(self, ps, **kw):
        args = dict(origin=(18.52, 73.85), radius_km=10, now_local=datetime(2026, 10, 13, 14, 0),
                    safe_until_local=datetime(2026, 10, 13, 17, 0), buffer_min=20, history={})
        args.update(kw)
        return places.rank(ps, **args)

    def test_closer_contactable_places_rank_first(self):
        ranked = self._rank([self._place("far", 0.06), self._place("near", 0.01), self._place("near-no-phone", 0.01, phone=None)])
        assert ranked[0].place.name == "near"
        assert any("No phone" in n for n in next(s for s in ranked if s.place.name == "near-no-phone").notes)

    def test_places_that_cannot_arrive_in_time_go_last_with_a_reason(self):
        ranked = self._rank([self._place("too-far", 0.3), self._place("ok", 0.05)],
                            safe_until_local=datetime(2026, 10, 13, 15, 0))
        assert ranked[-1].place.name == "too-far" and not ranked[-1].fits_window
        assert "too far" in ranked[-1].notes[0]

    def test_history_lifts_a_place_and_failures_lower_it(self):
        a, b = self._place("a", 0.02), self._place("b", 0.02)
        ranked = self._rank([a, b], history={"osm:node/b": places.History(given=3)})
        assert ranked[0].place.name == "b"
        ranked = self._rank([a, b], history={"osm:node/a": places.History(fell_through=2)})
        assert ranked[0].place.name == "b" and "Fell through 2 times" in ranked[1].notes[-1]

    def test_ranking_is_stable_under_permutation(self):
        ps = [self._place(n, d) for n, d in (("w", 0.01), ("x", 0.02), ("y", 0.02), ("z", 0.03))]
        assert [s.place.ref for s in self._rank(ps)] == [s.place.ref for s in self._rank(list(reversed(ps)))]

    def test_duplicates_of_the_same_place_are_merged(self):
        a = self._place("Same Kitchen", 0.01, phone=None)
        b = places.Place(ref="osm:way/2", name="same kitchen", kind="community_kitchen", lat=a.lat + 0.0005, lon=a.lon,
                         phone="+91 9")
        merged = places.dedupe([a, b])
        assert len(merged) == 1 and merged[0].phone == "+91 9"


class TestDishPictures:
    def test_titles_matching_the_dish_score_highest(self):
        from foodwise.services.dish_images import score_title

        names = ["Dal tadka", "Yellow dal"]
        good = score_title(names, "File:Dal tadka.JPG", 1024, 683)
        busy = score_title(names, "File:Butter Chicken with Dal Tadka, Air Arabia, 2025 (01).jpg", 3704, 2406)
        making = score_title(names, "File:Dal tadka making at a hotel.jpg", 2000, 1500)
        tiny = score_title(names, "File:Dal tadka.jpg", 200, 150)
        assert good > busy and good > making and good > tiny
        assert good >= 0.9 and making < 0.7

    def test_wikipedia_lead_image_is_trusted(self):
        from foodwise.services.dish_images import score_title

        article = score_title(["Chole"], "File:Chana masala.jpg", 800, 600, from_article=True)
        assert article >= 0.8  # the article's own photo is trusted even when its file name differs
        assert article > score_title(["Chole"], "File:Chole kulche street stall.jpg", 800, 600)

    def test_processing_crops_square_and_drops_metadata(self):
        import io

        from PIL import Image

        from foodwise.services.dish_images import process

        img = Image.new("RGB", (1200, 700), (10, 200, 30))
        exif = Image.Exif()
        exif[0x010E] = "where this was taken"
        buf = io.BytesIO()
        img.save(buf, "JPEG", exif=exif)
        out = process(buf.getvalue())
        assert b"where this was taken" not in out
        assert Image.open(io.BytesIO(out)).size == (480, 480)


class TestDataQuality:
    def test_fuzzy_dish_matching(self):
        dishes = [("1", "Vegetable pulao", []), ("2", "Paneer butter masala", ["PBM"])]
        assert match_dish("Veg Pulao", dishes).dish_id == "1"
        assert match_dish("Panner Butter Masala", dishes).status in ("exact", "suggested")
        assert match_dish("Chocolate cake", dishes).status == "new"

    def test_similarity_is_symmetric_enough(self):
        assert similarity("Dal tadka", "Daal Tadka") >= 0.9

    def _row(self, n, **kw):
        base = dict(_row=n, date=date(2026, 8, 1) + timedelta(days=n), canteen_id="c", meal="lunch", dish_id="d",
                    unit="plates", prepared_qty=110, served_qty=100, leftover_qty=10)
        base.update(kw)
        return base

    def test_hard_rules(self):
        rows = [self._row(1), self._row(2, prepared_qty=50), self._row(3, served_qty=-1), self._row(4, meal="brunch")]
        report = check_rows(rows, known_meals={"lunch"}, dish_units={"d": "plates"})
        codes = {i.code for i in report.issues}
        assert {"prepared_lt_served", "negative_qty", "unknown_meal"} <= codes
        assert report.valid_rows == 1

    def test_duplicates_and_units(self):
        rows = [self._row(1), self._row(1), self._row(2, unit="kg")]
        report = check_rows(rows, known_meals={"lunch"}, dish_units={"d": "plates"})
        codes = [i.code for i in report.issues]
        assert "duplicate" in codes and "unit_change" in codes

    def test_outliers_and_copy_paste(self):
        rows = [self._row(i, served_qty=100 + (i % 3), prepared_qty=110 + (i % 3)) for i in range(1, 12)]
        rows += [self._row(20 + i, prepared_qty=120, served_qty=110) for i in range(8)]
        rows.append(self._row(40, served_qty=900, prepared_qty=950))
        codes = [i.code for i in check_rows(rows, known_meals={"lunch"}, dish_units={}).issues]
        assert "outlier" in codes and "constant_run" in codes

    def test_quality_score_drops_with_errors(self):
        good = check_rows([self._row(i) for i in range(1, 20)], known_meals={"lunch"}, dish_units={})
        bad = check_rows([self._row(i, prepared_qty=1) for i in range(1, 20)], known_meals={"lunch"}, dish_units={})
        assert good.score > bad.score


class TestReadiness:
    tiers = DEFAULT_SETTINGS["tiers"]

    @pytest.mark.parametrize(("days", "tier"), [(0, 0), (13, 0), (14, 1), (59, 1), (60, 2), (179, 2), (180, 3)])
    def test_tiers(self, days, tier):
        assert readiness(days, self.tiers).tier == tier

    def test_message_says_what_unlocks_next(self):
        assert readiness(23, self.tiers).message == "23 of 60 days. Full model selection unlocks at 60 days."


class TestPreference:
    def test_sparse_dish_shrinks_to_the_canteen_mean(self):
        today = date(2026, 10, 1)
        s = bayesian_score([(today, 5, 1.0)] * 3, today, prior_mean=3.5)
        assert 3.5 < s.score < 4.2

    def test_recent_opinions_count_more(self):
        today = date(2026, 10, 1)
        old_bad = [(today - timedelta(days=60), 1, 1.0)] * 20 + [(today, 5, 1.0)] * 20
        assert bayesian_score(old_bad, today, prior_mean=3).score > 4

    def test_future_opinions_are_ignored(self):
        today = date(2026, 10, 1)
        s = bayesian_score([(today + timedelta(days=1), 1, 1.0)] * 50, today, prior_mean=3.5)
        assert s.score == 3.5 and s.n == 0

    def test_diagnosis(self):
        assert diagnose(2.6, 3.5, 0.2, 0.0) == "replace_or_reduce"
        assert diagnose(4.4, 3.5, 0.02, 0.4) == "increase"
        assert diagnose(3.5, 3.5, 0.05, 0.05) is None


class TestTextAnalysis:
    def test_english_negative_with_aspect(self):
        r = analyze("The dal was cold and too salty")
        assert r.sentiment < 0
        assert "temperature" in r.aspects and "taste" in r.aspects

    def test_hinglish_positive(self):
        r = analyze("paneer bahut accha tha, ekdum mast")
        assert r.sentiment > 0 and r.language == "hinglish"

    def test_negation(self):
        assert analyze("not good at all").sentiment < 0

    def test_hindi_script(self):
        r = analyze("खाना ठंडा था")
        assert r.language == "hi" and r.sentiment < 0

    def test_pii_and_profanity_are_scrubbed(self):
        clean, pii, profane = scrub("call me on 9876543210 or mail a.b@x.com, shit food")
        assert "9876543210" not in clean and "a.b@x.com" not in clean and "shit" not in clean
        assert pii and profane

    def test_spam_scoring(self):
        assert spam_score("aaaaaaaaaaaaa", 0, 0) >= 0.5
        assert spam_score("nice food", 0, 0) == 0


class TestIncidentStateMachine:
    def test_allowed_and_forbidden_moves(self):
        assert can_transition("open", "arranged")
        assert can_transition("arranged", "open")  # an arrangement fell through
        assert not can_transition("closed", "open")
        assert not can_transition("rejected", "arranged")

    def test_transition_raises_on_illegal_move(self):
        from foodwise.core.errors import Conflict

        class Inc:
            status = "closed"

        with pytest.raises(Conflict):
            transition(Inc(), "arranged")


def test_settings_merge_keeps_defaults():
    merged = deep_merge(DEFAULT_SETTINGS, {"costs": {"shortage_to_waste_ratio": 9}})
    assert merged["costs"]["shortage_to_waste_ratio"] == 9
    assert merged["costs"]["disposal_cost_per_kg"] == DEFAULT_SETTINGS["costs"]["disposal_cost_per_kg"]
