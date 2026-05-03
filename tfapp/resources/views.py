from datetime import datetime, timedelta

from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from accounts.models import ProfileCredentialDocument, UserCareerRoleInterest, UserProfile
from accounts.views import _credential_display_context
from attendance.models import CustomUser, RoleChoices

from .forms import ResourceEventForm
from .models import EmployeeHandbook, EventAttachment, Policy, ResourceEvent


def _is_executive(user):
    return user.is_authenticated and getattr(user, "role", None) == RoleChoices.EXECUTIVE


@login_required
def resources_home(request):
    users = (
        CustomUser.objects.filter(is_active=True)
        .order_by("payroll_lastname", "payroll_firstname", "last_name", "first_name", "username")
    )
    policies = Policy.objects.all().order_by("title")
    handbook = EmployeeHandbook.objects.filter(pk=1).first()
    return render(
        request,
        "resources/home.html",
        {
            "directory_users": users,
            "policies": policies,
            "handbook": handbook,
            "is_executive": _is_executive(request.user),
        },
    )


@login_required
def user_detail(request, user_slug):
    u = get_object_or_404(CustomUser, public_slug=user_slug, is_active=True)
    profile = UserProfile.objects.filter(user=u).first()
    interests = list(
        UserCareerRoleInterest.objects.filter(user=u).select_related("role").order_by("role__sort_order", "role__name")
    )
    cred_list = list(
        ProfileCredentialDocument.objects.filter(user=u).order_by("display_order", "id")
    )
    cred_ctx = _credential_display_context(cred_list)
    cred_ctx["credential_documents_ordered"] = cred_list
    return render(
        request,
        "resources/user_detail.html",
        {
            "subject": u,
            "profile": profile,
            "career_interests": interests,
            **cred_ctx,
        },
    )


@login_required
def handbook_download(request):
    hb = EmployeeHandbook.objects.filter(pk=1).first()
    if not hb or not hb.pdf:
        raise Http404("Handbook not available.")
    return FileResponse(hb.pdf.open("rb"), as_attachment=True, filename="employee-handbook.pdf")


@login_required
def policy_detail(request, policy_slug):
    policy = get_object_or_404(Policy, slug=policy_slug)
    return render(request, "resources/policy_detail.html", {"policy": policy})


@login_required
@user_passes_test(_is_executive)
def event_add(request):
    if request.method == "POST":
        form = ResourceEventForm(request.POST)
        files = request.FILES.getlist("attachments")
        if form.is_valid():
            ev = form.save(commit=False)
            ev.created_by = request.user
            ev.save()
            for f in files[:20]:
                if not f.content_type or not f.content_type.startswith("image/"):
                    continue
                if f.size > 10 * 1024 * 1024:
                    continue
                EventAttachment.objects.create(event=ev, image=f)
            return redirect(reverse("resources:home") + "#events")
    else:
        form = ResourceEventForm()
    return render(request, "resources/event_form.html", {"form": form})


@login_required
def events_feed(request):
    """JSON for FullCalendar (list of event dicts)."""
    events = []
    tz = timezone.get_current_timezone()
    for ev in ResourceEvent.objects.all().order_by("event_date", "event_time", "pk"):
        if ev.all_day or not ev.event_time:
            start = ev.event_date.isoformat()
            all_day = True
            end = None
        else:
            dt = datetime.combine(ev.event_date, ev.event_time)
            if timezone.is_naive(dt):
                dt = timezone.make_aware(dt, tz)
            start = dt.isoformat()
            end_dt = dt + timedelta(hours=1)
            end = end_dt.isoformat()
            all_day = False
        title = (ev.title or "Event").strip() or "Event"
        row = {
            "id": ev.pk,
            "title": title,
            "start": start,
            "allDay": all_day,
            "url": reverse("resources:event_detail", args=[ev.pk]),
            "extendedProps": {
                "details": ev.details[:500],
                "hasAttachments": ev.attachments.exists(),
            },
        }
        if end:
            row["end"] = end
        events.append(row)
    return JsonResponse(events, safe=False)


@login_required
def event_detail(request, pk):
    ev = get_object_or_404(ResourceEvent, pk=pk)
    return render(request, "resources/event_detail.html", {"event": ev})


@login_required
def event_detail_json(request, pk):
    ev = get_object_or_404(ResourceEvent, pk=pk)
    atts = [{"url": a.image.url, "name": a.image.name.rsplit("/", 1)[-1]} for a in ev.attachments.all()]
    return JsonResponse(
        {
            "id": ev.pk,
            "title": ev.title or "Event",
            "event_date": ev.event_date.isoformat(),
            "event_time": ev.event_time.strftime("%H:%M") if ev.event_time else None,
            "all_day": ev.all_day,
            "details": ev.details,
            "attachments": atts,
        }
    )
