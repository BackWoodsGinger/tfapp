from django.shortcuts import render

from .models import HomeTickerItem
from .overlay_plugins import get_home_overlay_context


def index(request):
    segments = []
    for item in HomeTickerItem.objects.filter(is_active=True):
        msg = (item.message or "").strip()
        if msg:
            segments.append(msg)
    if not segments:
        segments = ["Welcome to TF-R App"]
    return render(
        request,
        "pages/index.html",
        {
            "ticker_segments": segments,
            "overlay_plugins": get_home_overlay_context(),
        },
    )
