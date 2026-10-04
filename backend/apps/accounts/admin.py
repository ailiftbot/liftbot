from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User

from .models import OTP, UserProfile


class UserProfileInline(admin.StackedInline):
    model = UserProfile
    can_delete = False
    fields = ('full_name', 'is_verified', 'email_verified')


class UserAdmin(BaseUserAdmin):
    inlines = [UserProfileInline]

    def get_inline_instances(self, request, obj=None):
        # The profile is created by a post_save signal; only edit it on change.
        if obj is None:
            return []
        return super().get_inline_instances(request, obj)


admin.site.unregister(User)
admin.site.register(User, UserAdmin)


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'full_name', 'is_verified', 'email_verified')
    list_filter = ('is_verified', 'email_verified')
    search_fields = ('full_name', 'user__email')


@admin.register(OTP)
class OTPAdmin(admin.ModelAdmin):
    list_display = ('user', 'code', 'is_used', 'expires_at', 'created_at')
    list_filter = ('is_used',)
    search_fields = ('user__email', 'code')
    readonly_fields = ('created_at',)
