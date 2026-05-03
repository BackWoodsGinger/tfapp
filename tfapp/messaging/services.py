from django.db import transaction
from django.db.models import Count

from attendance.models import CustomUser

from .models import Conversation, ConversationParticipant


def find_or_create_dm(user_a: CustomUser, user_b: CustomUser) -> Conversation:
    """Return the existing non-group conversation between two users, or create it."""
    if user_a.pk == user_b.pk:
        raise ValueError("Cannot create a DM with the same user twice.")

    conv_ids = ConversationParticipant.objects.filter(user=user_a).values_list(
        "conversation_id", flat=True
    )
    candidates = (
        Conversation.objects.filter(id__in=conv_ids, is_group=False)
        .annotate(pc=Count("participants"))
        .filter(pc=2)
    )
    for conv in candidates:
        if ConversationParticipant.objects.filter(conversation=conv, user=user_b).exists():
            return conv

    with transaction.atomic():
        conv = Conversation.objects.create(is_group=False, name="")
        ConversationParticipant.objects.create(conversation=conv, user=user_a)
        ConversationParticipant.objects.create(conversation=conv, user=user_b)
        return conv


def user_is_participant(user, conversation: Conversation) -> bool:
    return ConversationParticipant.objects.filter(conversation=conversation, user=user).exists()
