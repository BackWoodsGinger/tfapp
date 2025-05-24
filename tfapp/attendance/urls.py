from django.urls import path
from . import views
from django.contrib.auth import views as auth_views
from .views import close_payroll
app_name = 'attendance'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path("occurrences/<str:filter_by>/", views.attendance_list, name="attendance_list_filtered"),
    path("occurrences/", views.attendance_list, name="attendance_list"),
    path("reports/", views.reports_view, name="reports"),
    path("reports/generate/", views.generate_report_pdf, name="generate_report_pdf"),
    path("close-payroll/", views.close_payroll, name="close_payroll"),
    path("accounts/password_change/", auth_views.PasswordChangeView.as_view(template_name="registration/password_change.html"), name="password_change"),
    path("accounts/password_change/done/", auth_views.PasswordChangeDoneView.as_view(template_name="registration/password_change_done.html"), name="password_change_done"),
]