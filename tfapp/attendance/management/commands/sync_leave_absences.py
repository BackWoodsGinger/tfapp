from django.core.management.base import BaseCommand

from attendance.services.leave_service import sync_all_leaves


class Command(BaseCommand):
    help = (
        "Create the daily absence for every ongoing leave (FMLA, Leave of Absence, Disability) "
        "through today. Safe to run repeatedly; schedule nightly from cron."
    )

    def handle(self, *args, **options):
        result = sync_all_leaves()
        self.stdout.write(
            f"Leave sync: {result.created} created, {result.removed} removed, "
            f"{result.skipped_finalized} skipped (payroll week finalized)."
        )
