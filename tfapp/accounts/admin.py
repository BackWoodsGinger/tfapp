from django.contrib import admin

from .models import (
    CareerRole,
    ProfileCredentialDocument,
    ProfileUpdateReviewItem,
    UserCareerRoleInterest,
    UserProfile,
)


@admin.register(CareerRole)
class CareerRoleAdmin(admin.ModelAdmin):
    list_display = ("name", "sort_order", "is_active")
    list_editable = ("sort_order", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name",)


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "phone", "has_photo")
    search_fields = ("user__username", "user__email", "phone", "bio")
    raw_id_fields = ("user",)

    @admin.display(boolean=True, description="Photo")
    def has_photo(self, obj):
        return bool(obj.photo)


@admin.register(UserCareerRoleInterest)
class UserCareerRoleInterestAdmin(admin.ModelAdmin):
    list_display = ("user", "role")
    list_filter = ("role",)
    search_fields = ("user__username", "role__name")
    raw_id_fields = ("user",)


@admin.register(ProfileCredentialDocument)
class ProfileCredentialDocumentAdmin(admin.ModelAdmin):
    list_display = ("user", "title", "display_order", "uploaded_at")
    list_editable = ("display_order",)
    list_filter = ("user",)
    ordering = ("user", "display_order", "id")
    search_fields = ("user__username", "title")
    raw_id_fields = ("user",)
    readonly_fields = ("uploaded_at",)


@admin.register(ProfileUpdateReviewItem)
class ProfileUpdateReviewItemAdmin(admin.ModelAdmin):
    list_display = ("user", "update_type", "status", "created_at", "reviewed_at", "reviewed_by")
    list_filter = ("status", "update_type")
    search_fields = (
        "user__username",
        "photo_name_snapshot",
        "credential_title_snapshot",
        "credential_name_snapshot",
        "review_notes",
    )
    raw_id_fields = ("user", "profile", "credential_document", "reviewed_by")
    readonly_fields = ("created_at", "reviewed_at")
