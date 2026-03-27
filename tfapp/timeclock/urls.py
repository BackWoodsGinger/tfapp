from django.urls import path
from . import views

app_name = "timeclock"

urlpatterns = [
    path('', views.timeclock_home, name='timeclock_home'),
    path("edit/<slug:slug>/", views.edit_entry, name="edit_entry"),
]