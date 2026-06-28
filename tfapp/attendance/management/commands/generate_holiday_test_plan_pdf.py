"""
Generate the July 4, 2026 holiday pay manual test plan PDF.

Usage:
    python manage.py generate_holiday_test_plan_pdf
    python manage.py generate_holiday_test_plan_pdf --output /path/to/plan.pdf
"""
from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand

from attendance.reports.holiday_test_plan_pdf import write_holiday_test_plan_pdf


class Command(BaseCommand):
    help = "Write the July 4, 2026 holiday pay manual test plan PDF."

    def add_arguments(self, parser):
        default_out = Path(__file__).resolve().parents[4] / "docs" / "july-4-2026-holiday-pay-test-plan.pdf"
        parser.add_argument(
            "--output",
            type=str,
            default=str(default_out),
            help=f"Output PDF path (default: {default_out})",
        )

    def handle(self, *args, **options):
        output = Path(options["output"])
        write_holiday_test_plan_pdf(output)
        self.stdout.write(self.style.SUCCESS(f"Wrote {output}"))
