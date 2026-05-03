from django.urls import path

from . import views

app_name = "resources"

urlpatterns = [
    path("", views.resources_home, name="home"),
    path("directory/<slug:user_slug>/", views.user_detail, name="user_detail"),
    path("handbook/download/", views.handbook_download, name="handbook_download"),
    path("policies/<slug:policy_slug>/", views.policy_detail, name="policy_detail"),
    path("events/add/", views.event_add, name="event_add"),
    path("events/feed/", views.events_feed, name="events_feed"),
    path("events/<int:pk>/", views.event_detail, name="event_detail"),
    path("events/<int:pk>/json/", views.event_detail_json, name="event_detail_json"),
]
