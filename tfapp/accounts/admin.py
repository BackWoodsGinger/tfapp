from django.contrib import admin

from .models import CareerRole, ProfileCredentialDocument, UserCareerRoleInterest, UserProfile


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
