from django.conf import settings
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone


class Conversation(models.Model):
    """A direct (1:1) or group chat."""

    is_group = models.BooleanField(default=False)
    name = models.CharField(
        max_length=200,
        blank=True,
        help_text="Required for groups; ignored for direct messages.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        if self.is_group and self.name:
            return self.name
        return f"Conversation {self.pk}"

    def title_for(self, viewer):
        if self.is_group:
            return self.name.strip() or "Group chat"
        others = [
            p.user
            for p in self.participants.select_related("user").all()
            if p.user_id != viewer.pk
        ]
        if len(others) == 1:
            return others[0].payroll_display_name()
        if not others:
            return "Direct message"
        return ", ".join(u.payroll_display_name() for u in others[:3]) + ("…" if len(others) > 3 else "")


class ConversationParticipant(models.Model):
    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="participants",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="conversation_memberships",
    )
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["conversation", "user"],
                name="messaging_participant_unique",
            )
        ]
        ordering = ["joined_at"]

    def __str__(self):
        return f"{self.user_id} in {self.conversation_id}"


class Message(models.Model):
    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="sent_chat_messages",
    )
    ciphertext = models.TextField(help_text="Fernet-encrypted UTF-8 body.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"Message {self.pk} in {self.conversation_id}"


@receiver(post_save, sender=Message)
def _bump_conversation_timestamp(sender, instance, **kwargs):
    Conversation.objects.filter(pk=instance.conversation_id).update(updated_at=timezone.now())
