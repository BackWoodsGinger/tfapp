from .models import UserSession
from .session_utils import register_user_session


class UserSessionTrackingMiddleware:
    """
    Ensures authenticated requests have a UserSession row (covers admin login and
    legacy sessions) and enforces the concurrent session limit when a new row appears.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            sid = request.session.session_key
            if sid:
                existing = UserSession.objects.filter(session_key=sid).first()
                if not existing:
                    register_user_session(request.user, sid)
                elif existing.user_id != request.user.id:
                    # Session key reused across users (should not happen); reset mapping.
                    existing.delete()
                    register_user_session(request.user, sid)
        response = self.get_response(request)
        return response
