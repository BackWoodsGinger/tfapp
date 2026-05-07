from pages.models import HomeTickerSubmission

from .models import ProfileUpdateReviewItem


def executive_user_updates(request):
    if not request.user.is_authenticated or getattr(request.user, "role", None) != "executive":
        return {"executive_user_updates": None}

    ticker_pending = HomeTickerSubmission.objects.filter(
        status=HomeTickerSubmission.Status.PENDING
    ).count()
    profile_pending = ProfileUpdateReviewItem.objects.filter(
        status=ProfileUpdateReviewItem.Status.PENDING
    ).count()
    return {
        "executive_user_updates": {
            "ticker_pending": ticker_pending,
            "profile_pending": profile_pending,
            "total_pending": ticker_pending + profile_pending,
        }
    }
