# Backfill pto_hours_applied and personal_hours_applied for occurrences applied before we tracked the split

from datetime import date

from django.db import migrations


def _allocation_for_date(service_date, as_of_date):
    """PTO allocation (hours) for the service year containing as_of_date, based on tenure."""
    if not service_date:
        return 0
    years = (as_of_date - service_date).days // 365
    if 2 <= years <= 4:
        return 80
    if 5 <= years <= 8:
        return 100
    if 9 <= years <= 14:
        return 120
    if 15 <= years <= 20:
        return 140
    if 21 <= years <= 24:
        return 160
    if years >= 25:
        return 180
    return 0


def _service_year_start(service_date, occurrence_date):
    """Start date of the service year that contains occurrence_date."""
    if not service_date:
        return occurrence_date
    # Anniversary in the year of occurrence (or prior if occurrence is before anniversary)
    anniv_this_year = date(occurrence_date.year, service_date.month, service_date.day)
    if occurrence_date >= anniv_this_year:
        return anniv_this_year
    return date(occurrence_date.year - 1, service_date.month, service_date.day)


def backfill_pto_personal_split(apps, schema_editor):
    Occurrence = apps.get_model("attendance", "Occurrence")
    CustomUser = apps.get_model("attendance", "CustomUser")

    for user in CustomUser.objects.all():
        if not user.service_date or user.is_exempt:
            continue
        service_date = user.service_date
        # Occurrences that were applied but have no split stored
        occurrences = list(
            Occurrence.objects.filter(
                user=user,
                pto_applied=True,
                pto_hours_applied=0,
                personal_hours_applied=0,
            )
            .exclude(duration_hours=0)
            .order_by("date")
        )
        if not occurrences:
            continue

        # Group by service year and process in order
        current_year_start = None
        pto_remaining = 0
        for occ in occurrences:
            year_start = _service_year_start(service_date, occ.date)
            if year_start != current_year_start:
                current_year_start = year_start
                pto_remaining = _allocation_for_date(service_date, year_start)
            pto_deducted = min(occ.duration_hours, pto_remaining)
            personal_deducted = occ.duration_hours - pto_deducted
            pto_remaining -= pto_deducted
            occ.pto_hours_applied = round(pto_deducted, 2)
            occ.personal_hours_applied = round(personal_deducted, 2)
            occ.save()


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("attendance", "0008_occurrence_pto_hours_applied_personal_hours_applied"),
    ]

    operations = [
        migrations.RunPython(backfill_pto_personal_split, noop),
    ]
