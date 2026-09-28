"""Tests ohne Login: die myfitnesspal-Bibliothek wird durch einen Fake-Client ersetzt,
der echte Day/Meal/Entry/Exercise-Objekte der Bibliothek zurückgibt."""

import datetime as dt
from collections import OrderedDict
from http.cookiejar import MozillaCookieJar

import anyio
import pytest
from mcp.server.fastmcp.exceptions import ToolError
from myfitnesspal.day import Day
from myfitnesspal.entry import Entry
from myfitnesspal.exceptions import MyfitnesspalLoginError
from myfitnesspal.exercise import Exercise
from myfitnesspal.meal import Meal

import server

GOALS = {"calories": 2000.0, "carbohydrates": 250.0, "fat": 65.0, "protein": 100.0}


def make_day(date: dt.date, calories: float = 1800.0) -> Day:
    breakfast = Meal(
        "Frühstück",
        [
            Entry(
                "Haferflocken, 50 g",
                {"calories": 190.0, "carbohydrates": 30.0, "fat": 3.5, "protein": 6.5},
            ),
            Entry(
                "Milch 1,5%, 200 ml",
                {"calories": 94.0, "carbohydrates": 10.0, "fat": 3.0, "protein": 7.0},
            ),
        ],
    )
    dinner = Meal(
        "Abendessen",
        [
            Entry(
                "Nudeln, 1 Portion",
                {
                    "calories": calories - 284.0,
                    "carbohydrates": 150.0,
                    "fat": 40.0,
                    "protein": 60.0,
                },
            )
        ],
    )
    exercises = [
        Exercise(
            "cardiovascular",
            [Entry("Laufen", {"minutes": 30.0, "calories burned": 300.0})],
        ),
        Exercise(
            "strength training",
            [Entry("Kniebeugen", {"sets": 3.0, "reps/set": 10.0, "weight/set": 60.0})],
        ),
    ]
    return Day(
        date=date,
        meals=[breakfast, dinner],
        goals=dict(GOALS),
        notes=lambda: "Guter Tag",
        water=lambda: 1500.0,
        exercises=lambda: exercises,
        complete=True,
    )


class FakeClient:
    def __init__(self):
        self.get_date_calls = 0

    def get_date(self, date):
        self.get_date_calls += 1
        if date.day % 2 == 0:  # gerade Tage: nichts geloggt
            return Day(date=date, meals=[Meal("Frühstück", [])], goals=dict(GOALS))
        return make_day(date, calories=1800.0 if date.day != 1 else 2500.0)

    def get_measurements(self, measurement="Weight", lower_bound=None, upper_bound=None):
        if measurement != "Weight":
            raise ValueError(f"Measurement '{measurement}' does not exist.")
        data = OrderedDict(
            [
                (dt.date(2026, 9, 15), 70.0),
                (dt.date(2026, 9, 8), 71.0),
                (dt.date(2026, 9, 1), 72.0),
            ]
        )
        return OrderedDict((d, v) for d, v in data.items() if lower_bound <= d <= upper_bound)


@pytest.fixture
def fake(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(server, "_create_client", lambda: client)
    server._reset_client()
    server._day_cache.clear()
    yield client
    server._reset_client()
    server._day_cache.clear()


def run(coro_fn, *args, **kwargs):
    return anyio.run(lambda: coro_fn(*args, **kwargs))


# ---------------------------------------------------------------------------


def test_lists_four_tools():
    tools = anyio.run(server.mcp.list_tools)
    assert sorted(t.name for t in tools) == [
        "get_diary",
        "get_exercises",
        "get_nutrition_summary",
        "get_weight_history",
    ]


def test_parse_date_keywords():
    today = dt.date.today()
    assert server._parse_date("heute") == today
    assert server._parse_date("Today") == today
    assert server._parse_date("gestern") == today - dt.timedelta(days=1)
    assert server._parse_date("yesterday") == today - dt.timedelta(days=1)
    assert server._parse_date("2026-09-01") == dt.date(2026, 9, 1)
    with pytest.raises(ToolError):
        server._parse_date("01.09.2026")


def test_get_diary(fake):
    result = run(server.get_diary, "2026-09-03")
    assert result["date"] == "2026-09-03"
    assert [m["name"] for m in result["meals"]] == ["Frühstück", "Abendessen"]
    oats = result["meals"][0]["entries"][0]
    assert oats["name"] == "Haferflocken"
    assert oats["quantity"] == 50.0
    assert oats["unit"] == "g"
    assert oats["nutrition"]["calories"] == 190.0
    assert result["totals"]["calories"] == 1800.0
    assert result["goals"]["calories"] == 2000.0
    assert result["remaining"]["calories"] == 200.0
    assert result["water_ml"] == 1500.0
    assert result["notes"] == "Guter Tag"


def test_diary_is_cached(fake):
    run(server.get_diary, "2026-09-03")
    run(server.get_diary, "2026-09-03")
    assert fake.get_date_calls == 1


def test_nutrition_summary(fake):
    result = run(server.get_nutrition_summary, "2026-09-01", "2026-09-07")
    assert result["days_in_range"] == 7
    assert result["days_logged"] == 4  # 1., 3., 5., 7.
    # (2500 + 3 * 1800) / 4
    assert result["average_per_logged_day"]["calories"] == 1975.0
    assert result["average_goals"]["calories"] == 2000.0
    assert result["days_within_calorie_goal"] == 3
    assert result["days_with_calorie_goal"] == 4
    pct = result["macro_distribution_percent"]
    assert round(sum(pct.values())) == 100
    assert len(result["days"]) == 7


def test_nutrition_summary_default_is_last_7_days(fake):
    result = run(server.get_nutrition_summary)
    assert result["end_date"] == dt.date.today().isoformat()
    assert result["days_in_range"] == 7


def test_nutrition_summary_too_long(fake):
    with pytest.raises(ToolError, match="Maximal 62"):
        run(server.get_nutrition_summary, "2026-01-01", "2026-06-01")


def test_weight_history(fake):
    result = run(server.get_weight_history, "2026-08-01", "2026-09-30")
    assert [v["value"] for v in result["values"]] == [72.0, 71.0, 70.0]
    stats = result["stats"]
    assert stats["first"]["value"] == 72.0
    assert stats["last"]["value"] == 70.0
    assert stats["change"] == -2.0
    assert stats["min"] == 70.0
    assert stats["max"] == 72.0
    assert stats["average"] == 71.0
    assert stats["change_per_week"] == -1.0


def test_weight_history_unknown_measurement(fake):
    with pytest.raises(ToolError, match="does not exist"):
        run(server.get_weight_history, "2026-08-01", "2026-09-30", "Unsinn")


def test_get_exercises(fake):
    result = run(server.get_exercises, "2026-09-03")
    assert result["cardio"] == [{"name": "Laufen", "minutes": 30.0, "calories burned": 300.0}]
    assert result["strength"][0]["name"] == "Kniebeugen"
    assert result["cardio_minutes_total"] == 30.0
    assert result["calories_burned_total"] == 300.0


def test_reconnects_once_then_german_error(monkeypatch):
    attempts = []

    def failing_client():
        attempts.append(1)
        raise MyfitnesspalLoginError("not logged in")

    monkeypatch.setattr(server, "_create_client", failing_client)
    server._reset_client()
    server._day_cache.clear()
    with pytest.raises(ToolError, match="neu anmelden"):
        run(server.get_diary, "2026-09-03")
    assert len(attempts) == 2


def test_missing_config(monkeypatch):
    monkeypatch.delenv("MFP_COOKIE_FILE", raising=False)
    monkeypatch.delenv("MFP_BROWSER", raising=False)
    with pytest.raises(ToolError, match="MFP_COOKIE_FILE"):
        server._load_cookies()


def test_cookie_file_is_loaded(monkeypatch, tmp_path):
    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text(
        "# Netscape HTTP Cookie File\n"
        ".myfitnesspal.com\tTRUE\t/\tTRUE\t0\tsession\tabc123\n"
    )
    monkeypatch.setenv("MFP_COOKIE_FILE", str(cookie_file))
    monkeypatch.delenv("MFP_BROWSER", raising=False)
    jar = server._load_cookies()
    assert isinstance(jar, MozillaCookieJar)
    assert [c.value for c in jar] == ["abc123"]


def test_cookie_file_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("MFP_COOKIE_FILE", str(tmp_path / "gibtsnicht.txt"))
    with pytest.raises(ToolError, match="nicht gefunden"):
        server._load_cookies()
