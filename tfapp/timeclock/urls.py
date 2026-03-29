from django.urls import path
from . import views

app_name = "timeclock"

urlpatterns = [
    path('', views.timeclock_home, name='timeclock_home'),
    path("check-clock-in/", views.check_clock_in, name="check_clock_in"),
    path("edit/<slug:slug>/", views.edit_entry, name="edit_entry"),
]