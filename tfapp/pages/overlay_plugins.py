"""
Optional home overlay widgets: weather (Open-Meteo) and stock quotes (Stooq).
Configured via Django settings — no admin rows. Acts as lightweight “plugin” data for the index view.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings

logger = logging.getLogger(__name__)

# Open-Meteo WMO weathercode → Font Awesome 6 Free (fas) icon class (without "fa-" prefix in map values).
_WMO_FA_ICON = {
    0: "fa-sun",
    1: "fa-cloud-sun",
    2: "fa-cloud-sun",
    3: "fa-cloud",
    45: "fa-smog",
    48: "fa-smog",
    51: "fa-cloud-rain",
    53: "fa-cloud-rain",
    55: "fa-cloud-showers-heavy",
    56: "fa-cloud-rain",
    57: "fa-cloud-rain",
    61: "fa-cloud-rain",
    63: "fa-cloud-rain",
    65: "fa-cloud-showers-heavy",
    66: "fa-cloud-meatball",
    67: "fa-cloud-meatball",
    71: "fa-snowflake",
    73: "fa-snowflake",
    75: "fa-snowflake",
    77: "fa-snowflake",
    80: "fa-cloud-sun-rain",
    81: "fa-cloud-sun-rain",
    82: "fa-cloud-bolt",
    85: "fa-snowflake",
    86: "fa-snowflake",
    95: "fa-bolt",
    96: "fa-cloud-bolt",
    99: "fa-cloud-bolt",
}


def _fa_weather_icon(wmo_code: int | None) -> str:
    if wmo_code is None:
        return "fa-cloud-sun"
    try:
        code = int(wmo_code)
    except (TypeError, ValueError):
        return "fa-cloud-sun"
    return _WMO_FA_ICON.get(code, "fa-cloud-sun")


def _fa_stock_icon(api_symbol: str) -> str:
    s = (api_symbol or "").strip().upper()
    return "fa-chart-line" if s.startswith("^") else "fa-arrow-trend-up"


def _http_get_json(url: str) -> dict | None:
    timeout = getattr(settings, "OVERLAY_HTTP_TIMEOUT_SEC", 10)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "TF-R-App-Overlay/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as e:
        logger.warning("Overlay JSON fetch failed (%s): %s", url.split("?")[0], e)
        return None


def _http_get_text(url: str) -> str | None:
    timeout = getattr(settings, "OVERLAY_HTTP_TIMEOUT_SEC", 10)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "TF-R-App-Overlay/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        logger.warning("Overlay text fetch failed (%s): %s", url.split("?")[0], e)
        return None


def fetch_weather_plugin() -> dict | None:
    """Returns dict for template or None if weather overlay is disabled."""
    if not getattr(settings, "OVERLAY_WEATHER_ENABLED", True):
        return None
    lat = getattr(settings, "OVERLAY_WEATHER_LATITUDE", 45.06)
    lon = getattr(settings, "OVERLAY_WEATHER_LONGITUDE", -84.49)
    label = getattr(settings, "OVERLAY_WEATHER_LABEL", "Afton, MI")
    q = urllib.parse.urlencode(
        {
            "latitude": lat,
            "longitude": lon,
            "current_weather": "true",
            "temperature_unit": "fahrenheit",
            "daily": "temperature_2m_max,temperature_2m_min",
            "forecast_days": "1",
        }
    )
    url = f"https://api.open-meteo.com/v1/forecast?{q}"
    data = _http_get_json(url)
    if not data:
        return {
            "kind": "weather",
            "label": label,
            "error": "Unavailable",
            "temp_f": None,
            "high_f": None,
            "low_f": None,
            "fa_icon": "fa-circle-exclamation",
        }
    cw = data.get("current_weather") or {}
    temp = cw.get("temperature")
    high_f = low_f = None
    daily = data.get("daily") or {}
    highs = daily.get("temperature_2m_max") or []
    lows = daily.get("temperature_2m_min") or []
    if highs:
        try:
            high_f = float(highs[0])
        except (TypeError, ValueError, IndexError):
            pass
    if lows:
        try:
            low_f = float(lows[0])
        except (TypeError, ValueError, IndexError):
            pass
    if temp is None:
        return {
            "kind": "weather",
            "label": label,
            "error": "Unavailable",
            "temp_f": None,
            "high_f": high_f,
            "low_f": low_f,
            "fa_icon": "fa-circle-exclamation",
        }
    wmo = cw.get("weathercode")
    return {
        "kind": "weather",
        "label": label,
        "temp_f": float(temp),
        "high_f": high_f,
        "low_f": low_f,
        "error": None,
        "fa_icon": _fa_weather_icon(wmo),
    }


def _stooq_symbol(api_symbol: str) -> str:
    """Stooq query token: US indices use leading ^ and no .us; equities use .us."""
    s = (api_symbol or "").strip().upper()
    if not s:
        return ""
    if s.startswith("^"):
        return s.lower()
    return f"{s.replace('.', '-').lower()}.us"


def _stock_row(
    raw_sym: str,
    display_label: str,
    *,
    price: str | None,
    error: str | None,
) -> dict:
    r = (raw_sym or "").strip().upper()
    prefix = not r.startswith("^")
    return {
        "kind": "stock",
        "symbol": r,
        "label": display_label,
        "price": price,
        "error": error,
        "prefix_dollar": prefix,
        "fa_icon": _fa_stock_icon(r),
    }


def fetch_stock_plugin(api_symbol: str, display_label: str) -> dict:
    raw = (api_symbol or "").strip().upper()
    if not raw:
        return _stock_row("", display_label, price=None, error="Invalid symbol")
    stooq_sym = _stooq_symbol(raw)
    if not stooq_sym:
        return _stock_row(raw, display_label, price=None, error="Invalid symbol")
    qs = urllib.parse.urlencode({"s": stooq_sym, "f": "sd2t2ohlcv", "h": "", "e": "csv"})
    url = f"https://stooq.com/q/l/?{qs}"
    text = _http_get_text(url)
    if not text:
        return _stock_row(raw, display_label, price=None, error="Unavailable")
    try:
        reader = csv.reader(io.StringIO(text))
        rows = list(reader)
        if len(rows) < 2:
            return _stock_row(raw, display_label, price=None, error="Unavailable")
        last = rows[-1]
        if len(last) < 7:
            return _stock_row(raw, display_label, price=None, error="Unavailable")
        close = last[6].strip()
        if not close or close == "N/D":
            return _stock_row(raw, display_label, price=None, error="Unavailable")
        return _stock_row(raw, display_label, price=close, error=None)
    except (IndexError, ValueError) as e:
        logger.debug("Stooq parse failed for %s: %s", raw, e)
        return _stock_row(raw, display_label, price=None, error="Unavailable")


def get_home_overlay_context() -> dict:
    """Context for the index template: weather widget + list of stock widgets."""
    weather = fetch_weather_plugin()
    quotes = getattr(settings, "OVERLAY_STOCK_QUOTES", [])
    stocks = [fetch_stock_plugin(sym, lbl) for sym, lbl in quotes] if quotes else []
    return {"weather": weather, "stocks": stocks}
