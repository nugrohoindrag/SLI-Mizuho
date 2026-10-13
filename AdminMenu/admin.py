from django.contrib import admin
from .models import Menu

@admin.register(Menu)
class MenuAdmin(admin.ModelAdmin):
    list_display = ['name', 'link', 'parent_display', 'level', 'permissions_display']
    list_filter = ['level']
    search_fields = ['name', 'link', 'icon_css', 'icon_html']

    def parent_display(self, obj):
        return obj.parent.name if obj.parent else '-'
    parent_display.short_description = 'Parent'

    def permissions_display(self, obj):
        return ", ".join([p.display_name for p in obj.permissions.all()]) if obj.permissions.exists() else '-'
    permissions_display.short_description = 'Permissions'
