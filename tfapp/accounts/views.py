from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages, auth
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import PasswordChangeDoneView, PasswordChangeView
from django.db import transaction
from django.db.models import Max
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from .forms import ProfileCredentialDocumentForm, UserProfileForm
from .models import (
    CareerRole,
    ProfileCredentialDocument,
    ProfileUpdateReviewItem,
    UserCareerRoleInterest,
    UserProfile,
)
from .session_utils import register_user_session

def _credential_display_context(documents_ordered_newest_first):
    image_docs = []
    other_docs = []
    for d in documents_ordered_newest_first:
        if d.is_web_image():
            image_docs.append(d)
        else:
            other_docs.append(d)
    return {
        "credential_image_documents": image_docs,
        "credential_non_image_documents": other_docs,
    }


def _safe_next_redirect_url(request):
    next_url = (request.POST.get("next") or request.GET.get("next") or "").strip()
    if not next_url:
        return settings.LOGIN_REDIRECT_URL
    if url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return next_url
    return settings.LOGIN_REDIRECT_URL


def _is_executive(user):
    return user.is_authenticated and getattr(user, "role", None) == "executive"


def _queue_profile_photo_review_item(user, profile_obj):
    if not profile_obj.photo:
        return
    ProfileUpdateReviewItem.objects.create(
        user=user,
        update_type=ProfileUpdateReviewItem.UpdateType.PROFILE_PHOTO,
        profile=profile_obj,
        photo_name_snapshot=profile_obj.photo.name or "",
    )


def _queue_credential_review_item(user, document):
    ProfileUpdateReviewItem.objects.create(
        user=user,
        update_type=ProfileUpdateReviewItem.UpdateType.CREDENTIAL_UPLOAD,
        credential_document=document,
        credential_title_snapshot=document.title or "",
        credential_name_snapshot=document.file.name or "",
    )


def login(request):
    if request.method == "POST":
        username = request.POST["username"]
        password = request.POST["password"]

        user = auth.authenticate(username=username, password=password)

        if user is not None:
            auth.login(request, user)
            register_user_session(user, request.session.session_key)
            messages.success(request, "You are now logged in")
            return redirect(_safe_next_redirect_url(request))
        messages.error(request, "Invalid credentials")
        next_q = request.POST.get("next") or request.GET.get("next")
        if next_q:
            q = urlencode({"next": next_q})
            return redirect(f"{reverse('login')}?{q}")
        return redirect("login")
    return render(request, "accounts/login.html")


def logout(request):
    if request.method == "POST":
        auth.logout(request)
        messages.success(request, "You are now logged out")
        return redirect("index")
    return redirect("index")


@login_required
def profile(request):
    profile_obj, _ = UserProfile.objects.get_or_create(user=request.user)
    career_roles = list(CareerRole.objects.filter(is_active=True).order_by("sort_order", "name"))
    documents_list = list(
        ProfileCredentialDocument.objects.filter(user=request.user).order_by("display_order", "id")
    )
    doc_display_ctx = _credential_display_context(documents_list)
    doc_display_ctx["credential_documents_ordered"] = documents_list
    career_interests = list(
        UserCareerRoleInterest.objects.filter(user=request.user).select_related("role")
    )

    if request.method == "POST":
        action = request.POST.get("action") or ""

        if action == "save_profile":
            post_data = request.POST
            if profile_obj.bio and "bio" not in request.POST:
                post_data = request.POST.copy()
                post_data["bio"] = profile_obj.bio
            previous_photo_name = ""
            if profile_obj.photo:
                previous_photo_name = profile_obj.photo.name or ""
            form = UserProfileForm(post_data, request.FILES, instance=profile_obj)
            if form.is_valid():
                saved_profile = form.save()
                updated_photo_name = ""
                if saved_profile.photo:
                    updated_photo_name = saved_profile.photo.name or ""
                if updated_photo_name and updated_photo_name != previous_photo_name:
                    _queue_profile_photo_review_item(request.user, saved_profile)
                messages.success(request, "Profile updated.")
                return redirect("profile")
            messages.error(request, "Please correct the errors below.")
            return render(
                request,
                "accounts/profile.html",
                {
                    "profile_obj": profile_obj,
                    "profile_form": form,
                    "document_form": ProfileCredentialDocumentForm(),
                    "career_roles": career_roles,
                    "career_interests": career_interests,
                    **doc_display_ctx,
                },
            )

        if action == "save_bio":
            profile_obj.bio = request.POST.get("bio", "")
            profile_obj.save(update_fields=["bio"])
            messages.success(request, "Bio updated.")
            return redirect("profile")

        if action == "upload_document":
            doc_form = ProfileCredentialDocumentForm(request.POST, request.FILES)
            if doc_form.is_valid():
                doc = doc_form.save(commit=False)
                doc.user = request.user
                agg = ProfileCredentialDocument.objects.filter(user=request.user).aggregate(
                    m=Max("display_order")
                )
                max_ord = agg["m"]
                doc.display_order = (max_ord + 1) if max_ord is not None else 0
                doc.save()
                _queue_credential_review_item(request.user, doc)
                messages.success(request, "Document uploaded.")
                return redirect("profile")
            messages.error(request, "Upload failed; check the form.")
            return render(
                request,
                "accounts/profile.html",
                {
                    "profile_obj": profile_obj,
                    "profile_form": UserProfileForm(instance=profile_obj),
                    "document_form": doc_form,
                    "career_roles": career_roles,
                    "career_interests": career_interests,
                    **doc_display_ctx,
                },
            )

        if action == "delete_document":
            doc_id = request.POST.get("document_id")
            doc = get_object_or_404(ProfileCredentialDocument, pk=doc_id, user=request.user)
            doc.file.delete(save=False)
            doc.delete()
            messages.success(request, "Document removed.")
            return redirect("profile")

        if action == "reorder_credentials":
            raw = (request.POST.get("credential_order") or "").strip()
            parts = [p.strip() for p in raw.split(",") if p.strip().isdigit()]
            try:
                ids = [int(p) for p in parts]
            except ValueError:
                ids = []
            owned = set(
                ProfileCredentialDocument.objects.filter(user=request.user).values_list("pk", flat=True)
            )
            if not ids or len(ids) != len(owned) or set(ids) != owned:
                messages.error(request, "Invalid reorder request.")
                return redirect("profile")
            with transaction.atomic():
                for i, pk in enumerate(ids):
                    ProfileCredentialDocument.objects.filter(pk=pk, user=request.user).update(
                        display_order=i
                    )
            messages.success(request, "Certificate order updated.")
            return redirect("profile")

        if action == "save_interests":
            raw_ids = request.POST.getlist("interest_role")
            id_set = []
            for s in raw_ids:
                s = (s or "").strip()
                if not s:
                    continue
                try:
                    pk = int(s)
                except ValueError:
                    continue
                id_set.append(pk)
            id_set = list(dict.fromkeys(id_set))
            valid_ids = set(
                CareerRole.objects.filter(pk__in=id_set, is_active=True).values_list("pk", flat=True)
            )
            chosen = [pk for pk in id_set if pk in valid_ids]
            with transaction.atomic():
                UserCareerRoleInterest.objects.filter(user=request.user).delete()
                UserCareerRoleInterest.objects.bulk_create(
                    [
                        UserCareerRoleInterest(user=request.user, role_id=rid)
                        for rid in chosen
                    ]
                )
            messages.success(request, "Career interests saved.")
            return redirect("profile")

        messages.error(request, "Unknown action.")
        return redirect("profile")

    return render(
        request,
        "accounts/profile.html",
        {
            "profile_obj": profile_obj,
            "profile_form": UserProfileForm(instance=profile_obj),
            "document_form": ProfileCredentialDocumentForm(),
            "career_roles": career_roles,
            "career_interests": career_interests,
            **doc_display_ctx,
        },
    )


@login_required
def profile_updates_review(request):
    if not _is_executive(request.user):
        messages.error(request, "Only executives can review profile updates.")
        return redirect("attendance:dashboard")

    if request.method == "POST":
        item_id = request.POST.get("item_id")
        action = (request.POST.get("action") or "").strip()
        notes = (request.POST.get("review_notes") or "").strip()
        if not item_id:
            messages.error(request, "Missing review item.")
            return redirect("profile_updates_review")
        try:
            item_id = int(item_id)
        except (TypeError, ValueError):
            messages.error(request, "Invalid review item.")
            return redirect("profile_updates_review")

        with transaction.atomic():
            item = (
                ProfileUpdateReviewItem.objects.select_for_update()
                .select_related("profile", "credential_document")
                .filter(pk=item_id, status=ProfileUpdateReviewItem.Status.PENDING)
                .first()
            )
            if not item:
                messages.error(request, "That update is no longer pending.")
                return redirect("profile_updates_review")

            if action == "remove_photo":
                prof = item.profile
                if prof and prof.photo:
                    prof.photo.delete(save=False)
                    prof.photo = None
                    prof.save(update_fields=["photo"])
                item.review_notes = notes or "Removed profile photo during review."
            elif action == "remove_document":
                doc = item.credential_document
                if doc:
                    doc.file.delete(save=False)
                    doc.delete()
                item.review_notes = notes or "Removed uploaded document during review."
            elif action == "approve":
                item.review_notes = notes
            else:
                messages.error(request, "Invalid review action.")
                return redirect("profile_updates_review")

            item.status = ProfileUpdateReviewItem.Status.REVIEWED
            item.reviewed_by = request.user
            item.reviewed_at = timezone.now()
            item.save(update_fields=["status", "reviewed_by", "reviewed_at", "review_notes"])

        messages.success(request, "Profile update reviewed.")
        return redirect("profile_updates_review")

    pending_items = (
        ProfileUpdateReviewItem.objects.filter(status=ProfileUpdateReviewItem.Status.PENDING)
        .select_related("user", "profile", "credential_document")
        .order_by("created_at")
    )
    recent_items = (
        ProfileUpdateReviewItem.objects.exclude(status=ProfileUpdateReviewItem.Status.PENDING)
        .select_related("user", "reviewed_by")
        .order_by("-reviewed_at", "-created_at")[:25]
    )
    return render(
        request,
        "accounts/profile_updates_review.html",
        {
            "pending_items": pending_items,
            "recent_items": recent_items,
        },
    )


class PasswordChange(PasswordChangeView):
    template_name = "accounts/password_change.html"
    success_url = reverse_lazy("password_change_done")


class PasswordChangeDone(PasswordChangeDoneView):
    template_name = "accounts/password_change_done.html"
