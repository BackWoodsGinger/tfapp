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
        return {"kind": "weather", "label": label, "error": "Unavailable", "temp_f": None, "high_f": None, "low_f": None}
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
        }
    return {
        "kind": "weather",
        "label": label,
        "temp_f": float(temp),
        "high_f": high_f,
        "low_f": low_f,
        "error": None,
    }


def _stooq_symbol(api_symbol: str) -> str:
    """Stooq query token: US indices use leading ^ and no .us; equities use .us."""
    s = (api_symbol or "").strip().upper()
    if not s:
        return ""
    if s.startswith("^"):
        return s.lower()
    return f"{s.replace('.', '-').lower()}.us"


def fetch_stock_plugin(api_symbol: str, display_label: str) -> dict:
    raw = (api_symbol or "").strip().upper()
    if not raw:
        return {
            "kind": "stock",
            "symbol": "",
            "label": display_label,
            "price": None,
            "error": "Invalid symbol",
            "prefix_dollar": False,
        }
    stooq_sym = _stooq_symbol(raw)
    if not stooq_sym:
        return {
            "kind": "stock",
            "symbol": raw,
            "label": display_label,
            "price": None,
            "error": "Invalid symbol",
            "prefix_dollar": not raw.startswith("^"),
        }
    qs = urllib.parse.urlencode({"s": stooq_sym, "f": "sd2t2ohlcv", "h": "", "e": "csv"})
    url = f"https://stooq.com/q/l/?{qs}"
    text = _http_get_text(url)
    if not text:
        return {
            "kind": "stock",
            "symbol": raw,
            "label": display_label,
            "price": None,
            "error": "Unavailable",
            "prefix_dollar": not raw.startswith("^"),
        }
    try:
        reader = csv.reader(io.StringIO(text))
        rows = list(reader)
        if len(rows) < 2:
            return {
                "kind": "stock",
                "symbol": raw,
                "label": display_label,
                "price": None,
                "error": "Unavailable",
                "prefix_dollar": not raw.startswith("^"),
            }
        last = rows[-1]
        if len(last) < 7:
            return {
                "kind": "stock",
                "symbol": raw,
                "label": display_label,
                "price": None,
                "error": "Unavailable",
                "prefix_dollar": not raw.startswith("^"),
            }
        close = last[6].strip()
        if not close or close == "N/D":
            return {
                "kind": "stock",
                "symbol": raw,
                "label": display_label,
                "price": None,
                "error": "Unavailable",
                "prefix_dollar": not raw.startswith("^"),
            }
        return {
            "kind": "stock",
            "symbol": raw,
            "label": display_label,
            "price": close,
            "error": None,
            "prefix_dollar": not raw.startswith("^"),
        }
    except (IndexError, ValueError) as e:
        logger.debug("Stooq parse failed for %s: %s", raw, e)
        return {
            "kind": "stock",
            "symbol": raw,
            "label": display_label,
            "price": None,
            "error": "Unavailable",
            "prefix_dollar": not raw.startswith("^"),
        }


def get_home_overlay_context() -> dict:
    """Context for the index template: weather widget + list of stock widgets."""
    weather = fetch_weather_plugin()
    quotes = getattr(settings, "OVERLAY_STOCK_QUOTES", [])
    stocks = [fetch_stock_plugin(sym, lbl) for sym, lbl in quotes] if quotes else []
    return {"weather": weather, "stocks": stocks}
