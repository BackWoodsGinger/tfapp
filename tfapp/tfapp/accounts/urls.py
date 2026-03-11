from django.urls import path
from .views import PasswordChange, PasswordChangeDone, profile

from . import views
from attendance import views as attendance_views
urlpatterns = [
    path('login', views.login, name='login'),
    path('logout', views.logout, name='logout'),
    path('profile', profile, name='profile'),
    path('password_change', PasswordChange.as_view(), name='password_change'),
    path('password_change/done/', PasswordChangeDone.as_view(), name='password_change_done'),
]
