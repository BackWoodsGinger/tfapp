from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "accounts"

    def ready(self):
        from django.contrib.sessions.models import Session
        from django.db.models.signals import post_delete

        def _sync_user_session_on_session_delete(sender, instance, **kwargs):
            from .models import UserSession

            UserSession.objects.filter(session_key=instance.session_key).delete()

        post_delete.connect(_sync_user_session_on_session_delete, sender=Session)
