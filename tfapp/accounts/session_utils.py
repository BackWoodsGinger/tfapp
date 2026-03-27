from django.contrib.sessions.backends.db import SessionStore

from .models import UserSession

# Oldest sessions beyond this count are deleted (Django session row + cookie invalidation).
MAX_SESSIONS_PER_USER = 3


def register_user_session(user, session_key: str) -> None:
    """Record this browser session for the user and trim excess sessions."""
    if not session_key:
        return
    UserSession.objects.update_or_create(
        session_key=session_key,
        defaults={"user": user},
    )
    _trim_oldest_sessions(user, keep_session_key=session_key)


def _trim_oldest_sessions(user, keep_session_key: str) -> None:
    while UserSession.objects.filter(user=user).count() > MAX_SESSIONS_PER_USER:
        victim = (
            UserSession.objects.filter(user=user)
            .exclude(session_key=keep_session_key)
            .order_by("created_at")
            .first()
        )
        if not victim:
            break
        # Deletes django_session row; accounts.apps removes matching UserSession via post_delete.
        SessionStore(victim.session_key).delete()
