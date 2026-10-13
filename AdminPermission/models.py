from django.db import models
from django.utils import timezone

class Permission(models.Model):
    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=100, unique=True)
    display_name = models.CharField(max_length=150, null=True, blank=True)
    description = models.TextField(null=True, blank=True)

    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'permissions'
        managed = False
        ordering = ['id']

    def __str__(self):
        return self.display_name or self.name