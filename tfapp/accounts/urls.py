from django.contrib.auth import views as auth_views
from django.urls import path, reverse_lazy

from . import views
from .views import PasswordChange, PasswordChangeDone, profile

urlpatterns = [
    path("login", views.login, name="login"),
    path("logout", views.logout, name="logout"),
    path("profile", profile, name="profile"),
    path("password_change", PasswordChange.as_view(), name="password_change"),
    path("password_change/done/", PasswordChangeDone.as_view(), name="password_change_done"),
    path(
        "password_reset/",
        auth_views.PasswordResetView.as_view(
            template_name="accounts/password_reset_form.html",
            email_template_name="registration/password_reset_email.html",
            subject_template_name="registration/password_reset_subject.txt",
            success_url=reverse_lazy("password_reset_done"),
        ),
        name="password_reset",
    ),
    path(
        "password_reset/done/",
        auth_views.PasswordResetDoneView.as_view(
            template_name="accounts/password_reset_done.html",
        ),
        name="password_reset_done",
    ),
    path(
        "reset/<uidb64>/<token>/",
        auth_views.PasswordResetConfirmView.as_view(
            template_name="accounts/password_reset_confirm.html",
            success_url=reverse_lazy("password_reset_complete"),
        ),
        name="password_reset_confirm",
    ),
    path(
        "reset/done/",
        auth_views.PasswordResetCompleteView.as_view(
            template_name="accounts/password_reset_complete.html",
        ),
        name="password_reset_complete",
    ),
]
