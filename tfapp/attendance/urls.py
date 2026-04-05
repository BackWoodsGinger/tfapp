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
    path(
        "dashboard/perfect-attendance-pdf/",
        views.perfect_attendance_pdf,
        name="perfect_attendance_pdf",
    ),
    path("close-payroll/", views.close_payroll, name="close_payroll"),
    path("unfinalize-payroll/", views.unfinalize_payroll, name="unfinalize_payroll"),
    path(
        "payroll/schedule-template.csv",
        views.payroll_schedule_csv_download,
        name="payroll_schedule_csv_download",
    ),
    path(
        "payroll/schedule-upload/",
        views.payroll_schedule_csv_upload,
        name="payroll_schedule_csv_upload",
    ),
    # Time off request and approval workflow
    path("timeoff/request/", views.request_time_off, name="request_time_off"),
    path("timeoff/mine/", views.my_time_off_requests, name="my_time_off_requests"),
    path(
        "timeoff/adjust-punch/",
        views.request_adjust_punch,
        name="request_adjust_punch",
    ),
    path(
        "timeoff/adjust-punch/week.json",
        views.adjust_punch_my_week_json,
        name="adjust_punch_my_week_json",
    ),
    path(
        "timeoff/adjust-punch/<slug:slug>/approve/",
        views.approve_adjust_punch,
        name="approve_adjust_punch",
    ),
    path(
        "timeoff/adjust-punch/<slug:slug>/deny/",
        views.deny_adjust_punch,
        name="deny_adjust_punch",
    ),
    path(
        "timeoff/adjust-punch/<slug:slug>/cancel/",
        views.cancel_adjust_punch,
        name="cancel_adjust_punch",
    ),
    path(
        "timeoff/work-through-lunch/",
        views.request_work_through_lunch,
        name="request_work_through_lunch",
    ),
    path(
        "timeoff/work-through-lunch/<slug:slug>/approve/",
        views.approve_work_through_lunch,
        name="approve_work_through_lunch",
    ),
    path(
        "timeoff/work-through-lunch/<slug:slug>/deny/",
        views.deny_work_through_lunch,
        name="deny_work_through_lunch",
    ),
    path(
        "timeoff/work-through-lunch/<slug:slug>/cancel/",
        views.cancel_work_through_lunch,
        name="cancel_work_through_lunch",
    ),
    path(
        "timeoff/team/",
        views.team_time_off_requests,
        name="team_time_off_requests",
    ),
    path(
        "timeoff/<slug:slug>/approve/",
        views.approve_time_off,
        name="approve_time_off",
    ),
    path(
        "timeoff/<slug:slug>/deny/",
        views.deny_time_off,
        name="deny_time_off",
    ),
    path(
        "timeoff/<slug:slug>/cancel/",
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