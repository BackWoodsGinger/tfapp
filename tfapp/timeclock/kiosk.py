"""Detect barcode timeclock kiosks by registered IP and/or secret token cookie."""

from __future__ import annotations

import os

from django.conf import settings
from django.http import HttpRequest, HttpResponse

from .models import TimeclockKioskIP, TimeclockKioskToken

KIOSK_COOKIE_NAME = "timeclock_kiosk"
# Long-lived so Chromium kiosk mode stays in badge-scan UI across reboots.
KIOSK_COOKIE_MAX_AGE = 60 * 60 * 24 * 365 * 10  # ~10 years


def _trust_forwarded_for() -> bool:
    """Only honor X-Forwarded-For when explicitly enabled (reverse proxy)."""
    return os.environ.get("DJANGO_TRUST_X_FORWARDED_FOR", "").lower() in (
        "1",
        "true",
        "yes",
    )


def get_client_ip(request: HttpRequest) -> str | None:
    """
    Client IP used for kiosk matching.

    Defaults to REMOTE_ADDR so visitors cannot spoof PIN-less punches via
    X-Forwarded-For. Set DJANGO_TRUST_X_FORWARDED_FOR=1 only when a trusted
    reverse proxy overwrites that header (then the leftmost address is used).
    """
    if _trust_forwarded_for():
        forwarded = (request.META.get("HTTP_X_FORWARDED_FOR") or "").strip()
        if forwarded:
            return forwarded.split(",")[0].strip() or None
    remote = (request.META.get("REMOTE_ADDR") or "").strip()
    return remote or None


def _active_token_value(raw: str | None) -> str | None:
    token = (raw or "").strip()
    if not token:
        return None
    if TimeclockKioskToken.objects.filter(token=token, is_active=True).exists():
        return token
    return None


def kiosk_token_from_query(request: HttpRequest) -> str | None:
    """Return active token from ?kiosk= if present and valid."""
    return _active_token_value(request.GET.get("kiosk"))


def kiosk_token_from_cookie(request: HttpRequest) -> str | None:
    """Return active token from the kiosk cookie if present and valid."""
    return _active_token_value(request.COOKIES.get(KIOSK_COOKIE_NAME))


def is_kiosk_ip(request: HttpRequest) -> bool:
    ip = get_client_ip(request)
    if not ip:
        return False
    return TimeclockKioskIP.objects.filter(ip_address=ip, is_active=True).exists()


def is_timeclock_kiosk(request: HttpRequest) -> bool:
    """True for registered kiosk IP or valid kiosk token (query or cookie)."""
    if is_kiosk_ip(request):
        return True
    if kiosk_token_from_cookie(request):
        return True
    if kiosk_token_from_query(request):
        return True
    return False


def kiosk_auth_method(request: HttpRequest) -> str:
    """Staff debug: 'ip', 'token', or 'off'."""
    if is_kiosk_ip(request):
        return "ip"
    if kiosk_token_from_cookie(request) or kiosk_token_from_query(request):
        return "token"
    return "off"


def set_kiosk_cookie(response: HttpResponse, token: str, request: HttpRequest) -> None:
    secure = bool(getattr(settings, "SESSION_COOKIE_SECURE", False) or request.is_secure())
    response.set_cookie(
        KIOSK_COOKIE_NAME,
        token,
        max_age=KIOSK_COOKIE_MAX_AGE,
        httponly=True,
        samesite="Lax",
        secure=secure,
        path="/",
    )


def clear_kiosk_cookie(response: HttpResponse) -> None:
    response.delete_cookie(KIOSK_COOKIE_NAME, path="/")
