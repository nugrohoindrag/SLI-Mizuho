from django.db import models
from django.utils import timezone
from django.conf import settings
from django.db.models import JSONField

class ActivityLogMU(models.Model):
    log_name = models.CharField(max_length=100)
    description = models.TextField()
    subject_id = models.IntegerField(null=True, blank=True)
    subject_type = models.CharField(max_length=150)
    
    causer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='activity_logs_mu'
    )
    causer_type = models.CharField(max_length=150)

    properties = JSONField(null=True, blank=True)
    
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"[{self.log_name}] {self.description}"

    class Meta:
        db_table = 'activity_logs_mu'
        ordering = ['-created_at']
        verbose_name = "Activity Log MU"
        verbose_name_plural = "Activity Logs MU"