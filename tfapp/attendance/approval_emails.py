"""
Email approvers (group lead, else supervisor) when employees submit or cancel
time off, work-through-lunch, or adjust-punch requests.
"""
from __future__ import annotations

import logging
import re
from typing import Optional
from urllib.parse import urljoin

from django.conf import settings
from django.core.mail import send_mail
from django.urls import reverse
from django.utils.html import escape
from django.utils import timezone as django_tz

from .models import CustomUser

logger = logging.getLogger(__name__)

_LOCAL_HOST_RE = re.compile(r"^(127\.\d+\.\d+\.\d+|localhost)$", re.I)


def _site_base_explicit() -> str:
    return (
        getattr(settings, "SITE_BASE_URL", None)
        or getattr(settings, "BASE_URL", None)
        or ""
    ).strip().rstrip("/")


def _site_base_inferred() -> str:
    """
    If SITE_BASE_URL is unset, derive https://host from a single non-wildcard ALLOWED_HOSTS entry.
    Skips localhost (cannot know port). Use SITE_BASE_URL for local dev with a real link.
    """
    hosts = [
        h.strip()
        for h in getattr(settings, "ALLOWED_HOSTS", [])
        if h and h != "*" and not h.startswith(".")
    ]
    if len(hosts) != 1:
        return ""
    host = hosts[0]
    if _LOCAL_HOST_RE.match(host.split(":")[0]):
        return ""
    scheme = "https" if getattr(settings, "SECURE_SSL_REDIRECT", False) else "http"
    return f"{scheme}://{host}"


def _site_base() -> str:
    return _site_base_explicit() or _site_base_inferred()


def team_requests_absolute_url() -> Optional[str]:
    """Full URL to the team approval queue, or None if the site origin cannot be determined."""
    path = reverse("attendance:team_time_off_requests")
    base = _site_base()
    if not base:
        return None
    return urljoin(base.rstrip("/") + "/", path.lstrip("/"))


def recipient_for_employee(employee) -> Optional[CustomUser]:
    """
    Notify group lead if set and has an email; otherwise supervisor if they have an email.
    """
    if getattr(employee, "group_lead_id", None):
        gl = employee.group_lead
        if gl and (getattr(gl, "email", None) or "").strip():
            return gl
    sup = employee.supervisor
    if sup and (getattr(sup, "email", None) or "").strip():
        return sup
    return None


def _send(to_email: str, subject: str, body_plain: str, body_html: str) -> None:
    try:
        send_mail(
            subject,
            body_plain,
            settings.DEFAULT_FROM_EMAIL,
            [to_email],
            html_message=body_html,
            fail_silently=False,
        )
    except Exception:
        logger.exception("Failed to send approval notification email to %s", to_email)


def _fmt_dt(dt):
    if not dt:
        return "—"
    return django_tz.localtime(dt).strftime("%Y-%m-%d %H:%M")


def _html_email(
    *,
    intro_plain: str,
    intro_html: str,
    detail_lines: list[str],
    link_label: str,
    abs_url: Optional[str],
    footer_plain: str,
) -> tuple[str, str]:
    """Build matching plain-text and HTML bodies (details escaped in HTML)."""
    details_plain = "\n".join(f"  {line}" for line in detail_lines)
    details_html_items = "".join(f"<li>{escape(line)}</li>" for line in detail_lines)

    if abs_url:
        safe_href = escape(abs_url)
        link_plain = f"{link_label}: {abs_url}"
        link_html = f'<p><a href="{safe_href}">{escape(link_label)}</a></p>'
    else:
        link_plain = (
            f"{link_label}: sign in to TF-R App, then open Attendance → Team time off requests "
            f"({reverse('attendance:team_time_off_requests')}). "
            "Set DJANGO_SITE_BASE_URL in the server environment for clickable links."
        )
        link_html = (
            "<p><em>Sign in to TF-R App and open <strong>Attendance → Team time off requests</strong>. "
            "Ask your admin to set <code>DJANGO_SITE_BASE_URL</code> for direct links in email.</em></p>"
        )

    plain = "\n".join(
        [
            intro_plain,
            "",
            "Details:",
            details_plain,
            "",
            footer_plain,
            "",
            link_plain,
        ]
    )
    html = f"""<!DOCTYPE html>
<html>
<body style="font-family: sans-serif; line-height: 1.45; color: #222;">
<p>{intro_html}</p>
<p><strong>Details</strong></p>
<ul style="margin-top:0;">{details_html_items}</ul>
<p style="color:#555;">{escape(footer_plain)}</p>
{link_html}
</body>
</html>"""
    return plain, html


def notify_time_off_submitted(tor) -> None:
    to_user = recipient_for_employee(tor.user)
    if not to_user:
        logger.warning(
            "No approver email for user %s; skipping time off submitted notification",
            tor.user_id,
        )
        return
    emp = tor.user.payroll_display_name()
    lines = [
        f"Type: Time off",
        f"Dates: {tor.start_date} to {tor.end_date}",
        f"Absence type: {tor.get_subtype_display()}",
    ]
    if tor.partial_day:
        lines.append(f"Partial day: {tor.partial_hours} hours")
    try:
        hrs = tor.compute_requested_hours()
        lines.append(f"Requested hours (scheduled): {hrs:.2f}")
    except Exception:
        pass
    if (tor.comments or "").strip():
        lines.append(f"Comments: {tor.comments.strip()[:2000]}")
    _notify_submitted(to_user.email, emp, "Time off", lines)


def notify_work_through_lunch_submitted(wtl) -> None:
    to_user = recipient_for_employee(wtl.user)
    if not to_user:
        logger.warning(
            "No approver email for user %s; skipping work-through-lunch submitted notification",
            wtl.user_id,
        )
        return
    emp = wtl.user.payroll_display_name()
    lines = [
        f"Type: Work through lunch",
        f"Work date: {wtl.work_date}",
    ]
    if (wtl.comments or "").strip():
        lines.append(f"Comments: {wtl.comments.strip()[:2000]}")
    _notify_submitted(to_user.email, emp, "Work-through lunch", lines)


def notify_adjust_punch_submitted(apr) -> None:
    to_user = recipient_for_employee(apr.user)
    if not to_user:
        logger.warning(
            "No approver email for user %s; skipping adjust punch submitted notification",
            apr.user_id,
        )
        return
    emp = apr.user.payroll_display_name()
    pf = apr.get_punch_field_display()
    lines = [
        f"Type: Time entry edit (adjust punch)",
        f"Date: {apr.time_entry.date}",
        f"Field: {pf}",
        f"Current value: {_fmt_dt(apr.previous_at)}",
        f"Requested value: {_fmt_dt(apr.requested_at)}",
    ]
    if (apr.comments or "").strip():
        lines.append(f"Comments: {apr.comments.strip()[:2000]}")
    _notify_submitted(to_user.email, emp, "Time entry edit", lines)


def _notify_submitted(to_email: str, employee_display: str, short_label: str, detail_lines: list[str]) -> None:
    subject = f"[TF-R App] New {short_label} request — {employee_display}"
    intro_plain = f"{employee_display} submitted a {short_label.lower()} request."
    intro_html = f"{escape(employee_display)} submitted a {escape(short_label.lower())} request."
    footer = "You are receiving this as their group lead or supervisor."
    url = team_requests_absolute_url()
    plain, html = _html_email(
        intro_plain=intro_plain,
        intro_html=intro_html,
        detail_lines=detail_lines,
        link_label="Open team approval queue",
        abs_url=url,
        footer_plain=footer,
    )
    _send(to_email, subject, plain, html)


def _notify_cancelled(
    to_email: str,
    employee_display: str,
    short_label: str,
    detail_lines: list[str],
    was_approved: bool,
) -> None:
    status_phrase = (
        "a previously approved request (balances or entries may have been reverted)."
        if was_approved
        else "a pending request."
    )
    subject = f"[TF-R App] {short_label} request cancelled — {employee_display}"
    intro_plain = f"{employee_display} cancelled {status_phrase}"
    intro_html = f"{escape(employee_display)} cancelled {escape(status_phrase)}"
    footer = "You are receiving this as their group lead or supervisor."
    url = team_requests_absolute_url()
    plain, html = _html_email(
        intro_plain=intro_plain,
        intro_html=intro_html,
        detail_lines=[f"Request type: {short_label}", *detail_lines],
        link_label="Open team approval queue",
        abs_url=url,
        footer_plain=footer,
    )
    _send(to_email, subject, plain, html)


def notify_time_off_cancelled(tor, *, was_approved: bool) -> None:
    to_user = recipient_for_employee(tor.user)
    if not to_user:
        logger.warning(
            "No approver email for user %s; skipping time off cancelled notification",
            tor.user_id,
        )
        return
    emp = tor.user.payroll_display_name()
    lines = [
        f"Dates: {tor.start_date} to {tor.end_date}",
        f"Absence type: {tor.get_subtype_display()}",
        f"Prior status: {'Approved' if was_approved else 'Pending'}",
    ]
    _notify_cancelled(to_user.email, emp, "Time off", lines, was_approved)


def notify_work_through_lunch_cancelled(wtl, *, was_approved: bool) -> None:
    to_user = recipient_for_employee(wtl.user)
    if not to_user:
        logger.warning(
            "No approver email for user %s; skipping work-through-lunch cancelled notification",
            wtl.user_id,
        )
        return
    emp = wtl.user.payroll_display_name()
    lines = [
        f"Work date: {wtl.work_date}",
        f"Prior status: {'Approved' if was_approved else 'Pending'}",
    ]
    _notify_cancelled(to_user.email, emp, "Work-through lunch", lines, was_approved)


def notify_adjust_punch_cancelled(apr) -> None:
    """Only pending adjust-punch requests can be cancelled in the app."""
    to_user = recipient_for_employee(apr.user)
    if not to_user:
        logger.warning(
            "No approver email for user %s; skipping adjust punch cancelled notification",
            apr.user_id,
        )
        return
    emp = apr.user.payroll_display_name()
    pf = apr.get_punch_field_display()
    lines = [
        f"Date: {apr.time_entry.date}",
        f"Field: {pf}",
        f"Requested value: {_fmt_dt(apr.requested_at)}",
        f"Prior status: Pending",
    ]
    _notify_cancelled(to_user.email, emp, "Time entry edit", lines, was_approved=False)
