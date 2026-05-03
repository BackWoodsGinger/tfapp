from django.contrib import admin

from .models import Conversation, ConversationParticipant, Message


class ParticipantInline(admin.TabularInline):
    model = ConversationParticipant
    extra = 0
    raw_id_fields = ("user",)


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ("id", "is_group", "name", "updated_at")
    list_filter = ("is_group",)
    search_fields = ("name",)
    inlines = [ParticipantInline]


@admin.register(ConversationParticipant)
class ConversationParticipantAdmin(admin.ModelAdmin):
    list_display = ("conversation", "user", "joined_at")
    list_filter = ("conversation",)
    raw_id_fields = ("user", "conversation")


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ("id", "conversation", "sender", "created_at")
    list_filter = ("conversation",)
    raw_id_fields = ("sender", "conversation")
    readonly_fields = ("created_at",)
