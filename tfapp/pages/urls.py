from django.urls import path, include
from . import views
from attendance import views as attendance_views

urlpatterns = [
    path('', views.index, name='index'),
]