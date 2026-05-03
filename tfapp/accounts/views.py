from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages, auth
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import PasswordChangeDoneView, PasswordChangeView
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.http import url_has_allowed_host_and_scheme

from .forms import ProfileCredentialDocumentForm, UserProfileForm
from .models import CareerRole, ProfileCredentialDocument, UserCareerRoleInterest, UserProfile
from .session_utils import register_user_session


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
    documents = ProfileCredentialDocument.objects.filter(user=request.user)
    career_interests = list(
        UserCareerRoleInterest.objects.filter(user=request.user).select_related("role")
    )

    if request.method == "POST":
        action = request.POST.get("action") or ""

        if action == "save_profile":
            form = UserProfileForm(request.POST, request.FILES, instance=profile_obj)
            if form.is_valid():
                form.save()
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
                    "credential_documents": documents,
                    "career_interests": career_interests,
                },
            )

        if action == "upload_document":
            doc_form = ProfileCredentialDocumentForm(request.POST, request.FILES)
            if doc_form.is_valid():
                doc = doc_form.save(commit=False)
                doc.user = request.user
                doc.save()
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
                    "credential_documents": documents,
                    "career_interests": career_interests,
                },
            )

        if action == "delete_document":
            doc_id = request.POST.get("document_id")
            doc = get_object_or_404(ProfileCredentialDocument, pk=doc_id, user=request.user)
            doc.file.delete(save=False)
            doc.delete()
            messages.success(request, "Document removed.")
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
            "credential_documents": documents,
            "career_interests": career_interests,
        },
    )


class PasswordChange(PasswordChangeView):
    template_name = "accounts/password_change.html"
    success_url = reverse_lazy("password_change_done")


class PasswordChangeDone(PasswordChangeDoneView):
    template_name = "accounts/password_change_done.html"
