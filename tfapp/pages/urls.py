from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("ticker/submit/", views.ticker_submit, name="ticker_submit"),
    path("ticker/review/", views.ticker_review, name="ticker_review"),
]