from django.urls import path

from . import views

app_name = "messaging"

urlpatterns = [
    path("", views.inbox, name="inbox"),
    path("start/<slug:user_slug>/", views.start_dm, name="start_dm"),
    path("group/new/", views.group_create, name="group_create"),
    path("c/<int:pk>/", views.thread, name="thread"),
]
