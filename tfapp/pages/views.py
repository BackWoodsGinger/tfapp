from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Max
from django.shortcuts import redirect, render
from django.utils import timezone

from attendance.models import RoleChoices

from .forms import HomeTickerSubmissionForm
from .models import HomeTickerItem, HomeTickerSubmission


def index(request):
    segments = []
    for item in HomeTickerItem.objects.filter(is_active=True):
        msg = (item.message or "").strip()
        if msg:
            segments.append(msg)
    if not segments:
        segments = ["Welcome to TF-R App"]
    return render(
        request,
        "pages/index.html",
        {
            "ticker_segments": segments,
        },
    )


@login_required
def ticker_submit(request):
    """Logged-in users propose a ticker message (pending executive approval)."""
    if request.method == "POST":
        form = HomeTickerSubmissionForm(request.POST)
        if form.is_valid():
            sub = form.save(commit=False)
            sub.status = HomeTickerSubmission.Status.PENDING
            sub.submitted_by = request.user
            sub.save()
            messages.success(
                request,
                "Thanks — your message was submitted for review. An executive will approve it before it appears on the ticker.",
            )
            return redirect("index")
    else:
        form = HomeTickerSubmissionForm()
    return render(request, "pages/ticker_submit.html", {"form": form})


def _is_executive(user):
    return user.is_authenticated and getattr(user, "role", None) == RoleChoices.EXECUTIVE


@login_required
def ticker_review(request):
    """List pending submissions and approve/reject (executives only)."""
    if not _is_executive(request.user):
        messages.error(request, "Only executives can review ticker submissions.")
        return redirect("attendance:dashboard")

    if request.method == "POST":
        action = request.POST.get("action")
        pk = request.POST.get("submission_id")
        if not pk or action not in ("approve", "reject"):
            messages.error(request, "Invalid request.")
            return redirect("ticker_review")
        try:
            pk = int(pk)
        except (TypeError, ValueError):
            messages.error(request, "Invalid submission.")
            return redirect("ticker_review")

        with transaction.atomic():
            sub = (
                HomeTickerSubmission.objects.select_for_update()
                .filter(pk=pk, status=HomeTickerSubmission.Status.PENDING)
                .first()
            )
            if not sub:
                messages.error(request, "That submission is no longer pending.")
                return redirect("ticker_review")

            if action == "reject":
                sub.status = HomeTickerSubmission.Status.REJECTED
                sub.reviewed_by = request.user
                sub.reviewed_at = timezone.now()
                sub.save(update_fields=["status", "reviewed_by", "reviewed_at"])
                messages.success(request, "Submission rejected.")
                return redirect("ticker_review")

            # approve
            msg = (sub.message or "").strip()
            if not msg:
                messages.error(request, "Message was empty.")
                return redirect("ticker_review")

            max_sort = HomeTickerItem.objects.aggregate(m=Max("sort_order"))["m"]
            next_order = (max_sort or 0) + 1
            HomeTickerItem.objects.create(message=msg, sort_order=next_order, is_active=True)
            sub.status = HomeTickerSubmission.Status.APPROVED
            sub.reviewed_by = request.user
            sub.reviewed_at = timezone.now()
            sub.save(update_fields=["status", "reviewed_by", "reviewed_at"])
            messages.success(request, "Message approved and added to the home ticker.")

        return redirect("ticker_review")

    pending = HomeTickerSubmission.objects.filter(status=HomeTickerSubmission.Status.PENDING).order_by(
        "created_at"
    )
    recent = (
        HomeTickerSubmission.objects.exclude(status=HomeTickerSubmission.Status.PENDING)
        .select_related("reviewed_by", "submitted_by")
        .order_by("-reviewed_at", "-created_at")[:25]
    )
    return render(
        request,
        "pages/ticker_review.html",
        {"pending_submissions": pending, "recent_submissions": recent},
    )
