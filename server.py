"""MCP-Server für MyFitnessPal (nur lesend).

Datenzugriff über die inoffizielle Bibliothek ``myfitnesspal``. Die Anmeldung
läuft über Browser-Cookies:

- ``MFP_COOKIE_FILE``: Pfad zu einer cookies.txt im Netscape-Format (Standard)
- ``MFP_BROWSER``: alternativ Cookies direkt aus chrome/firefox/safari/edge/brave lesen
"""

from __future__ import annotations

import datetime as dt
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.cookiejar import CookieJar, MozillaCookieJar
from pathlib import Path
from typing import Any, Callable, TypeVar

import anyio
import myfitnesspal
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from myfitnesspal.day import Day
from myfitnesspal.entry import Entry
from myfitnesspal.exceptions import MyfitnesspalError

T = TypeVar("T")

MFP_DOMAIN = "myfitnesspal.com"
SUPPORTED_BROWSERS = ("chrome", "firefox", "safari", "edge", "brave")
DAY_CACHE_TTL = 300  # Sekunden
MAX_SUMMARY_DAYS = 62
DEFAULT_SUMMARY_DAYS = 7
DEFAULT_WEIGHT_DAYS = 90
FETCH_WORKERS = 4

RELOGIN_HINT = (
    "Bitte im Browser bei myfitnesspal.com neu anmelden und die cookies.txt "
    "neu exportieren (Erweiterung „Get cookies.txt LOCALLY“). "
    "Danach Claude Desktop neu starten."
)

mcp = FastMCP("myfitnesspal")

_client: myfitnesspal.Client | None = None
_client_lock = threading.Lock()
_day_cache: dict[dt.date, tuple[float, Day]] = {}
_cache_lock = threading.Lock()


# --------------------------------------------------------------------------
# Anmeldung / Client
# --------------------------------------------------------------------------


def _load_cookies() -> CookieJar:
    cookie_file = os.environ.get("MFP_COOKIE_FILE", "").strip()
    browser = os.environ.get("MFP_BROWSER", "").strip().lower()

    if cookie_file:
        path = Path(cookie_file).expanduser()
        if not path.is_file():
            raise ToolError(
                f"Cookie-Datei nicht gefunden: {path}. "
                "Bitte den Pfad in MFP_COOKIE_FILE prüfen. " + RELOGIN_HINT
            )
        jar = MozillaCookieJar(str(path))
        try:
            jar.load(ignore_discard=True, ignore_expires=True)
        except Exception as exc:  # LoadError, OSError, ...
            raise ToolError(
                f"Cookie-Datei {path} konnte nicht gelesen werden ({exc}). "
                "Sie muss im Netscape-Format (cookies.txt) vorliegen. " + RELOGIN_HINT
            ) from exc
        return jar

    if browser:
        if browser not in SUPPORTED_BROWSERS:
            raise ToolError(
                f"MFP_BROWSER='{browser}' wird nicht unterstützt. "
                f"Erlaubt: {', '.join(SUPPORTED_BROWSERS)}."
            )
        import browser_cookie3

        try:
            return getattr(browser_cookie3, browser)(domain_name=MFP_DOMAIN)
        except Exception as exc:
            raise ToolError(
                f"Cookies konnten nicht aus {browser} gelesen werden ({exc}). "
                "Tipp: stattdessen MFP_COOKIE_FILE mit einer exportierten "
                "cookies.txt verwenden."
            ) from exc

    raise ToolError(
        "Keine Anmeldedaten konfiguriert. Bitte in der Claude-Desktop-Konfiguration "
        "die Umgebungsvariable MFP_COOKIE_FILE (Pfad zur cookies.txt) oder "
        "MFP_BROWSER setzen."
    )


def _create_client() -> myfitnesspal.Client:
    return myfitnesspal.Client(cookiejar=_load_cookies())


def _get_client() -> myfitnesspal.Client:
    global _client
    with _client_lock:
        if _client is None:
            _client = _create_client()
        return _client


def _reset_client() -> None:
    global _client
    with _client_lock:
        _client = None


def _call_with_retry(fn: Callable[[myfitnesspal.Client], T]) -> T:
    """Führt fn(client) aus; bei Fehlern wird einmal neu verbunden."""
    last_exc: Exception | None = None
    for _attempt in range(2):
        try:
            return fn(_get_client())
        except ToolError:
            raise
        except ValueError as exc:
            # Eingabefehler (z. B. unbekannte Messung) – Neuverbinden hilft nicht.
            if not isinstance(exc, MyfitnesspalError):
                raise ToolError(str(exc)) from exc
            last_exc = exc
        except Exception as exc:
            last_exc = exc
        _reset_client()

    raise ToolError(
        f"MyFitnessPal ist nicht erreichbar oder die Anmeldung ist abgelaufen "
        f"({type(last_exc).__name__}: {last_exc}). " + RELOGIN_HINT
    ) from last_exc


async def _run(fn: Callable[[myfitnesspal.Client], T]) -> T:
    return await anyio.to_thread.run_sync(_call_with_retry, fn)


def _get_day(client: myfitnesspal.Client, date: dt.date) -> Day:
    now = time.monotonic()
    with _cache_lock:
        cached = _day_cache.get(date)
        if cached and now - cached[0] < DAY_CACHE_TTL:
            return cached[1]
    day = client.get_date(date)
    with _cache_lock:
        _day_cache[date] = (time.monotonic(), day)
    return day


# --------------------------------------------------------------------------
# Hilfsfunktionen
# --------------------------------------------------------------------------


def _parse_date(value: str | None, default: dt.date | None = None) -> dt.date:
    if value is None or not str(value).strip():
        if default is None:
            raise ToolError("Bitte ein Datum angeben (YYYY-MM-DD, 'heute' oder 'gestern').")
        return default
    text = str(value).strip().lower()
    today = dt.date.today()
    if text in ("heute", "today"):
        return today
    if text in ("gestern", "yesterday"):
        return today - dt.timedelta(days=1)
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        raise ToolError(
            f"Ungültiges Datum '{value}'. Erlaubt: YYYY-MM-DD, 'heute'/'today', "
            "'gestern'/'yesterday'."
        ) from None


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), 1)
    except (TypeError, ValueError):
        return None


def _nutrition(values: dict[str, Any]) -> dict[str, float | None]:
    return {k: _num(v) for k, v in values.items()}


def _entry_to_dict(entry: Entry) -> dict[str, Any]:
    quantity = entry.quantity
    try:
        quantity = float(quantity) if quantity is not None else None
    except ValueError:
        pass
    return {
        "name": entry.short_name or entry.name,
        "full_name": entry.name,
        "quantity": quantity,
        "unit": entry.unit,
        "nutrition": _nutrition(entry.nutrition_information),
    }


def _is_logged(day: Day) -> bool:
    return any(True for _ in day.entries)


def _date_range(start: dt.date, end: dt.date) -> list[dt.date]:
    return [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]


# --------------------------------------------------------------------------
# Tools (alle nur lesend)
# --------------------------------------------------------------------------


@mcp.tool()
async def get_diary(date: str = "heute") -> dict[str, Any]:
    """Ernährungstagebuch eines Tages aus MyFitnessPal.

    Liefert alle Mahlzeiten mit Einträgen (Name, Menge, Einheit, Kalorien, Makros),
    die Tagessumme, die Ziele, den Rest bis zum Ziel, Wasser (ml) und Notizen.

    Args:
        date: Datum als YYYY-MM-DD oder "heute"/"today", "gestern"/"yesterday".
    """
    day_date = _parse_date(date)

    def fetch(client: myfitnesspal.Client) -> dict[str, Any]:
        day = _get_day(client, day_date)
        totals = _nutrition(day.totals)
        goals = _nutrition(day.goals)
        remaining = {
            k: round(goal - (totals.get(k) or 0), 1)
            for k, goal in goals.items()
            if goal is not None
        }
        try:
            water = _num(day.water)
        except Exception:
            water = None
        try:
            notes = str(day.notes or "")
        except Exception:
            notes = ""
        return {
            "date": day_date.isoformat(),
            "complete": day.complete,
            "meals": [
                {
                    "name": meal.name,
                    "entries": [_entry_to_dict(e) for e in meal.entries],
                    "totals": _nutrition(meal.totals),
                }
                for meal in day.meals
            ],
            "totals": totals,
            "goals": goals,
            "remaining": remaining,
            "water_ml": water,
            "notes": notes,
        }

    return await _run(fetch)


@mcp.tool()
async def get_nutrition_summary(
    start_date: str | None = None, end_date: str | None = None
) -> dict[str, Any]:
    """Nährwert-Zusammenfassung über einen Zeitraum (Standard: letzte 7 Tage, max. 62).

    Liefert Durchschnittswerte pro geloggtem Tag, durchschnittliche Ziele,
    Makro-Verteilung in % der Kalorien (Protein/Kohlenhydrate 4 kcal/g, Fett 9 kcal/g),
    die Anzahl Tage im Kalorienziel und die Werte jedes einzelnen Tages.

    Args:
        start_date: Startdatum (YYYY-MM-DD, "heute", "gestern"). Standard: vor 6 Tagen.
        end_date: Enddatum (YYYY-MM-DD, "heute", "gestern"). Standard: heute.
    """
    end = _parse_date(end_date, default=dt.date.today())
    start = _parse_date(start_date, default=end - dt.timedelta(days=DEFAULT_SUMMARY_DAYS - 1))
    if start > end:
        start, end = end, start
    dates = _date_range(start, end)
    if len(dates) > MAX_SUMMARY_DAYS:
        raise ToolError(
            f"Zeitraum zu lang ({len(dates)} Tage). Maximal {MAX_SUMMARY_DAYS} Tage möglich."
        )

    def fetch(client: myfitnesspal.Client) -> list[Day]:
        with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
            return list(pool.map(lambda d: _get_day(client, d), dates))

    days = await _run(fetch)

    per_day = []
    logged: list[Day] = []
    for day in days:
        is_logged = _is_logged(day)
        if is_logged:
            logged.append(day)
        per_day.append(
            {
                "date": day.date.isoformat(),
                "logged": is_logged,
                "totals": _nutrition(day.totals),
                "calorie_goal": _num(day.goals.get("calories")),
            }
        )

    def average(dicts: list[dict[str, Any]]) -> dict[str, float]:
        keys = {k for d in dicts for k in d}
        return {
            k: round(sum(float(d.get(k) or 0) for d in dicts) / len(dicts), 1)
            for k in sorted(keys)
        } if dicts else {}

    avg_totals = average([d.totals for d in logged])
    avg_goals = average([d.goals for d in logged if d.goals])

    macro_kcal = {
        "protein": avg_totals.get("protein", 0) * 4,
        "carbohydrates": avg_totals.get("carbohydrates", 0) * 4,
        "fat": avg_totals.get("fat", 0) * 9,
    }
    macro_sum = sum(macro_kcal.values())
    macro_pct = {
        k: round(v / macro_sum * 100, 1) if macro_sum else None
        for k, v in macro_kcal.items()
    }

    days_with_goal = [
        d for d in logged if d.goals.get("calories")
    ]
    within = sum(
        1 for d in days_with_goal
        if (d.totals.get("calories") or 0) <= d.goals["calories"]
    )

    return {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "days_in_range": len(dates),
        "days_logged": len(logged),
        "average_per_logged_day": avg_totals,
        "average_goals": avg_goals,
        "macro_distribution_percent": macro_pct,
        "days_within_calorie_goal": within,
        "days_with_calorie_goal": len(days_with_goal),
        "days": per_day,
    }


@mcp.tool()
async def get_weight_history(
    start_date: str | None = None,
    end_date: str | None = None,
    measurement: str = "Weight",
) -> dict[str, Any]:
    """Verlauf einer Körpermessung (Standard: Gewicht der letzten 90 Tage).

    Liefert alle Messwerte und eine Statistik: erster/letzter Wert, Änderung,
    Minimum, Maximum, Durchschnitt und Änderung pro Woche. Die Einheit entspricht
    der Einstellung im MyFitnessPal-Konto (kg oder lbs).

    Args:
        start_date: Startdatum (YYYY-MM-DD, "heute", "gestern"). Standard: vor 90 Tagen.
        end_date: Enddatum (YYYY-MM-DD, "heute", "gestern"). Standard: heute.
        measurement: Name der Messung wie in MyFitnessPal, z. B. "Weight", "Body Fat", "Waist".
    """
    end = _parse_date(end_date, default=dt.date.today())
    start = _parse_date(start_date, default=end - dt.timedelta(days=DEFAULT_WEIGHT_DAYS))
    if start > end:
        start, end = end, start

    raw = await _run(lambda c: c.get_measurements(measurement, start, end))
    values = sorted((d, float(v)) for d, v in raw.items())

    result: dict[str, Any] = {
        "measurement": measurement,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "count": len(values),
        "values": [{"date": d.isoformat(), "value": round(v, 2)} for d, v in values],
    }
    if not values:
        result["stats"] = None
        return result

    (first_date, first), (last_date, last) = values[0], values[-1]
    nums = [v for _, v in values]
    change = last - first
    span_days = (last_date - first_date).days
    result["stats"] = {
        "first": {"date": first_date.isoformat(), "value": round(first, 2)},
        "last": {"date": last_date.isoformat(), "value": round(last, 2)},
        "change": round(change, 2),
        "min": round(min(nums), 2),
        "max": round(max(nums), 2),
        "average": round(sum(nums) / len(nums), 2),
        "change_per_week": round(change / span_days * 7, 2) if span_days else None,
    }
    return result


@mcp.tool()
async def get_exercises(date: str = "heute") -> dict[str, Any]:
    """Trainingstagebuch eines Tages: Cardio und Krafttraining.

    Args:
        date: Datum als YYYY-MM-DD oder "heute"/"today", "gestern"/"yesterday".
    """
    day_date = _parse_date(date)

    def fetch(client: myfitnesspal.Client) -> dict[str, Any]:
        exercises = _get_day(client, day_date).exercises
        cardio: list[dict[str, Any]] = []
        strength: list[dict[str, Any]] = []
        for group in exercises:
            items = [
                {"name": e.name, **_nutrition(e.nutrition_information)}
                for e in group.entries
            ]
            if "strength" in group.name.lower():
                strength.extend(items)
            else:
                cardio.extend(items)
        return {
            "date": day_date.isoformat(),
            "cardio": cardio,
            "strength": strength,
            "cardio_minutes_total": round(sum(i.get("minutes") or 0 for i in cardio), 1),
            "calories_burned_total": round(
                sum(i.get("calories burned") or 0 for i in cardio), 1
            ),
        }

    return await _run(fetch)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
