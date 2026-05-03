from django.contrib import messages as django_messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET

from attendance.models import CustomUser

from .crypto import decrypt_message_body, encrypt_message_body
from .forms import GroupConversationForm, MessageComposeForm
from .models import Conversation, ConversationParticipant, Message
from .services import find_or_create_dm, user_is_participant


@login_required
def inbox(request):
    memberships = (
        ConversationParticipant.objects.filter(user=request.user)
        .select_related("conversation")
        .prefetch_related("conversation__participants__user")
        .order_by("-conversation__updated_at")
    )
    rows = []
    for part in memberships:
        c = part.conversation
        last = (
            Message.objects.filter(conversation=c)
            .order_by("-created_at")
            .select_related("sender")
            .first()
        )
        preview = ""
        if last:
            preview = decrypt_message_body(last.ciphertext)[:160]
        rows.append(
            {
                "conversation": c,
                "title": c.title_for(request.user),
                "last_preview": preview,
                "last_at": last.created_at if last else c.updated_at,
            }
        )
    return render(request, "messaging/inbox.html", {"rows": rows})


@login_required
@require_GET
def start_dm(request, user_slug):
    other = get_object_or_404(CustomUser, public_slug=user_slug, is_active=True)
    if other.pk == request.user.pk:
        django_messages.error(request, "You cannot start a conversation with yourself.")
        return redirect("resources:user_detail", user_slug=user_slug)
    conv = find_or_create_dm(request.user, other)
    return redirect("messaging:thread", pk=conv.pk)


@login_required
def group_create(request):
    if request.method == "POST":
        form = GroupConversationForm(request.POST, creator=request.user)
        if form.is_valid():
            name = form.cleaned_data["name"].strip()
            member_users = list(form.cleaned_data["members"])
            with transaction.atomic():
                c = Conversation.objects.create(is_group=True, name=name)
                ConversationParticipant.objects.create(conversation=c, user=request.user)
                for u in member_users:
                    ConversationParticipant.objects.create(conversation=c, user=u)
            django_messages.success(request, "Group conversation created.")
            return redirect("messaging:thread", pk=c.pk)
        django_messages.error(request, "Fix the errors below to create the group.")
    else:
        form = GroupConversationForm(creator=request.user)
    return render(request, "messaging/group_create.html", {"form": form})


@login_required
def thread(request, pk):
    conv = get_object_or_404(Conversation, pk=pk)
    if not user_is_participant(request.user, conv):
        raise PermissionDenied

    if request.method == "POST":
        form = MessageComposeForm(request.POST)
        if form.is_valid():
            body = form.cleaned_data["body"].strip()
            Message.objects.create(
                conversation=conv,
                sender=request.user,
                ciphertext=encrypt_message_body(body),
            )
            django_messages.success(request, "Message sent.")
            return redirect("messaging:thread", pk=pk)
        django_messages.error(request, "Message could not be sent.")
    else:
        form = MessageComposeForm()

    msg_rows = list(
        Message.objects.filter(conversation=conv).select_related("sender").order_by("created_at")
    )
    for m in msg_rows:
        m.body_plain = decrypt_message_body(m.ciphertext)

    participants = list(
        conv.participants.select_related("user").order_by(
            "user__payroll_lastname", "user__payroll_firstname", "user__username"
        )
    )
    return render(
        request,
        "messaging/thread.html",
        {
            "conversation": conv,
            "title": conv.title_for(request.user),
            "messages": msg_rows,
            "form": form,
            "participants": participants,
        },
    )
