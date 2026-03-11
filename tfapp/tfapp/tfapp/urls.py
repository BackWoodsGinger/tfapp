
from django.contrib import admin
from django.urls import path, include
from django.conf.urls.static import static
from attendance.views import home
from django.conf import settings

urlpatterns = [
    path('', include('pages.urls')),
    path('admin/', admin.site.urls),
    path('accounts/', include('accounts.urls')),
    path('attendance/', include('attendance.urls')),
    path('timeclock/', include('timeclock.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
