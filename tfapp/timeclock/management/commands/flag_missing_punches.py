"""
Nightly job (e.g. 2:30 AM):

1) For past days: if clock_in and clock_out exist but lunch punches are missing, insert
   lunch_out / lunch_in from the employee's scheduled lunch times (when those times fall
   within the actual clock_in–clock_out window). This matches payroll logic that assumes
   a lunch deduction without requiring manual lunch punches.

2) Remaining incomplete entries are flagged and admins may be emailed.

Run via cron, e.g.:
  30 2 * * * cd /path/to/project && python manage.py flag_missing_punches
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.core import mail
from django.conf import settings
from attendance.schedule_utils import scheduled_lunch_datetimes_for_entry, work_through_lunch_approved_for_day
from timeclock.models import TimeEntry


class Command(BaseCommand):
    help = (
        "Apply scheduled lunch punches when missing; then flag incomplete entries and optionally email admins."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Only list entries that would be updated or flagged; do not save or email.",
        )
        parser.add_argument(
            "--no-email",
            action="store_true",
            help="Flag entries but do not send email to admins.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        no_email = options["no_email"]
        now = timezone.now()
        today = now.date()

        candidates = (
            TimeEntry.objects.filter(
                date__lt=today,
                clock_in__isnull=False,
                clock_out__isnull=False,
                lunch_out__isnull=True,
                lunch_in__isnull=True,
            )
            .select_related("user")
        )

        filled = []
        for entry in candidates:
            if work_through_lunch_approved_for_day(entry.user, entry.date):
                continue
            times = scheduled_lunch_datetimes_for_entry(entry)
            if times is None:
                continue
            lunch_out_dt, lunch_in_dt = times
            if dry_run:
                filled.append(entry)
                self.stdout.write(
                    f"  Would apply scheduled lunch: {entry.user.get_full_name() or entry.user.username} "
                    f"on {entry.date} (lunch {lunch_out_dt} – {lunch_in_dt})"
                )
                continue
            entry.lunch_out = lunch_out_dt
            entry.lunch_in = lunch_in_dt
            entry.missing_punch_flagged = False
            entry.missing_punch_flagged_at = None
            entry.save(
                update_fields=[
                    "lunch_out",
                    "lunch_in",
                    "missing_punch_flagged",
                    "missing_punch_flagged_at",
                ]
            )
            filled.append(entry)
            self.stdout.write(
                self.style.SUCCESS(
                    f"  Applied scheduled lunch: {entry.user.get_full_name() or entry.user.username} on {entry.date}"
                )
            )

        if filled and not dry_run:
            self.stdout.write(self.style.SUCCESS(f"Filled scheduled lunch on {len(filled)} entr{'y' if len(filled) == 1 else 'ies'}."))
        elif dry_run and filled:
            self.stdout.write(f"Would fill scheduled lunch on {len(filled)} entr{'y' if len(filled) == 1 else 'ies'}.")

        # Clear stale missing_punch flags (e.g. work-through lunch approved after a prior night's flag)
        stale_flagged = TimeEntry.objects.filter(
            date__lt=today, missing_punch_flagged=True
        ).select_related("user")
        cleared_stale = 0
        for entry in stale_flagged:
            if entry.is_incomplete():
                continue
            if dry_run:
                self.stdout.write(
                    f"  Would clear stale missing-punch flag: "
                    f"{entry.user.get_full_name() or entry.user.username} on {entry.date}"
                )
                cleared_stale += 1
                continue
            entry.missing_punch_flagged = False
            entry.missing_punch_flagged_at = None
            entry.save(update_fields=["missing_punch_flagged", "missing_punch_flagged_at"])
            cleared_stale += 1
            self.stdout.write(
                self.style.SUCCESS(
                    f"  Cleared stale missing-punch flag: "
                    f"{entry.user.get_full_name() or entry.user.username} on {entry.date}"
                )
            )
        if cleared_stale and not dry_run:
            self.stdout.write(self.style.SUCCESS(f"Cleared {cleared_stale} stale missing-punch flag(s)."))

        # Entries that have at least one punch but not all (incomplete)
        incomplete = TimeEntry.objects.filter(date__lt=today).exclude(
            clock_in=None,
            lunch_out=None,
            lunch_in=None,
            clock_out=None,
        )
        to_flag = [e for e in incomplete if e.is_incomplete() and not e.missing_punch_flagged]

        if not to_flag:
            self.stdout.write(self.style.SUCCESS("No incomplete entries to flag."))
            if not filled:
                return
            return

        self.stdout.write(f"Found {len(to_flag)} incomplete entr{'y' if len(to_flag) == 1 else 'ies'} to flag.")

        if dry_run:
            for e in to_flag:
                self.stdout.write(
                    f"  Would flag: {e.user.get_full_name() or e.user.username} on {e.date} (id={e.pk})"
                )
            return

        for e in to_flag:
            e.missing_punch_flagged = True
            e.missing_punch_flagged_at = now
            e.save(update_fields=["missing_punch_flagged", "missing_punch_flagged_at"])
            self.stdout.write(f"  Flagged: {e.user.get_full_name() or e.user.username} on {e.date}")

        if no_email:
            self.stdout.write(self.style.SUCCESS("Flagged; no email sent (--no-email)."))
            return

        recipient_list = []
        if getattr(settings, "ADMINS", None):
            recipient_list = [email for _, email in settings.ADMINS]
        if not recipient_list:
            from django.contrib.auth import get_user_model

            User = get_user_model()
            recipient_list = list(User.objects.filter(is_superuser=True).values_list("email", flat=True))
            if not recipient_list:
                recipient_list = list(
                    User.objects.filter(is_staff=True, email__isnull=False)
                    .exclude(email="")
                    .values_list("email", flat=True)
                )

        if not recipient_list:
            self.stdout.write(
                self.style.WARNING("No admin emails (ADMINS or staff/superuser). Skipping email.")
            )
            return

        subject = f"[Timeclock] {len(to_flag)} incomplete time entr{'y' if len(to_flag) == 1 else 'ies'} flagged"
        body_lines = [
            "The following time entries have missing punches and were flagged:",
            "",
        ]
        for e in to_flag:
            body_lines.append(
                f"- {e.user.get_full_name() or e.user.username} on {e.date} (entry id {e.pk})"
            )
            if getattr(settings, "BASE_URL", None):
                from django.urls import reverse

                try:
                    path = reverse("timeclock:edit_entry", args=[e.slug])
                    body_lines.append(f"  Edit: {settings.BASE_URL.rstrip('/')}{path}")
                except Exception:
                    pass
        body = "\n".join(body_lines)

        try:
            mail.send_mail(
                subject=subject,
                message=body,
                from_email=settings.DEFAULT_FROM_EMAIL if getattr(settings, "DEFAULT_FROM_EMAIL", None) else None,
                recipient_list=recipient_list,
                fail_silently=True,
            )
            self.stdout.write(self.style.SUCCESS(f"Email sent to {len(recipient_list)} admin(s)."))
        except Exception as exc:
            self.stdout.write(self.style.WARNING(f"Email failed: {exc}"))
