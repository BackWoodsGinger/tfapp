"""Detect barcode timeclock kiosks by registered static client IP."""

from __future__ import annotations

import os

from django.http import HttpRequest

from .models import TimeclockKioskIP


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


def is_timeclock_kiosk(request: HttpRequest) -> bool:
    """True when the request originates from an active registered kiosk IP."""
    ip = get_client_ip(request)
    if not ip:
        return False
    return TimeclockKioskIP.objects.filter(ip_address=ip, is_active=True).exists()
