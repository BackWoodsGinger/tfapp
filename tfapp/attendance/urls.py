from django.urls import path
from . import views
from django.contrib.auth import views as auth_views
from .views import close_payroll, unfinalize_payroll

app_name = "attendance"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path(
        "occurrences/<str:filter_by>/",
        views.attendance_list,
        name="attendance_list_filtered",
    ),
    path("occurrences/", views.attendance_list, name="attendance_list"),
    # Payroll (formerly Reports)
    path("payroll/", views.payroll_view, name="payroll"),
    path("payroll/user-breakdown/", views.payroll_user_breakdown, name="payroll_user_breakdown"),
    # Back-compat: keep old URLs but redirect to Payroll
    path("reports/", views.reports_redirect, name="reports"),
    path("reports/generate/", views.generate_report_pdf, name="generate_report_pdf"),
    path("close-payroll/", views.close_payroll, name="close_payroll"),
    path("unfinalize-payroll/", views.unfinalize_payroll, name="unfinalize_payroll"),
    # Time off request and approval workflow
    path("timeoff/request/", views.request_time_off, name="request_time_off"),
    path("timeoff/mine/", views.my_time_off_requests, name="my_time_off_requests"),
    path(
        "timeoff/team/",
        views.team_time_off_requests,
        name="team_time_off_requests",
    ),
    path(
        "timeoff/<int:pk>/approve/",
        views.approve_time_off,
        name="approve_time_off",
    ),
    path(
        "timeoff/<int:pk>/deny/",
        views.deny_time_off,
        name="deny_time_off",
    ),
    path(
        "timeoff/<int:pk>/cancel/",
        views.cancel_time_off,
        name="cancel_time_off",
    ),
    path(
        "accounts/password_change/",
        auth_views.PasswordChangeView.as_view(
            template_name="registration/password_change.html"
        ),
        name="password_change",
    ),
    path(
        "accounts/password_change/done/",
        auth_views.PasswordChangeDoneView.as_view(
            template_name="registration/password_change_done.html"
        ),
        name="password_change_done",
    ),
]