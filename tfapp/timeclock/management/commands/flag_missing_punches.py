"""
Nightly job: find time entries with missing punches (incomplete), flag them, and notify admins.
Run at 2:30 AM via cron, e.g.:
  30 2 * * * cd /path/to/project && python manage.py flag_missing_punches
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.core import mail
from django.conf import settings

from timeclock.models import TimeEntry


class Command(BaseCommand):
    help = "Find incomplete time entries, set missing_punch_flagged, and email admins."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Only list entries that would be flagged; do not update or email.",
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

        # Entries that have at least one punch but not all (incomplete)
        incomplete = TimeEntry.objects.filter(
            date__lt=now.date(),  # only past days
        ).exclude(
            clock_in=None,
            lunch_out=None,
            lunch_in=None,
            clock_out=None,
        )
        # Filter to those that are actually incomplete (any field set but not all)
        to_flag = [e for e in incomplete if e.is_incomplete() and not e.missing_punch_flagged]

        if not to_flag:
            self.stdout.write(self.style.SUCCESS("No incomplete entries to flag."))
            return

        self.stdout.write(f"Found {len(to_flag)} incomplete entr{'y' if len(to_flag) == 1 else 'ies'}.")

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

        # Notify admins: use ADMINS if set, else staff/superuser emails
        recipient_list = []
        if getattr(settings, "ADMINS", None):
            recipient_list = [email for _, email in settings.ADMINS]
        if not recipient_list:
            from django.contrib.auth import get_user_model
            User = get_user_model()
            recipient_list = list(
                User.objects.filter(is_superuser=True).values_list("email", flat=True)
            )
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
                    path = reverse("timeclock:edit_entry", args=[e.pk])
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
