from django.db import models
from django.utils import timezone


class ApiClientKey(models.Model):
    SCOPE_READ = 'read'
    SCOPE_WRITE = 'write'
    SCOPE_CHOICES = [
        (SCOPE_READ, 'Read-only'),
        (SCOPE_WRITE, 'Read/Write'),
    ]

    name = models.CharField(max_length=100)
    key = models.CharField(max_length=64, unique=True, help_text='Shared secret presented in X-API-KEY header')
    scope = models.CharField(max_length=10, choices=SCOPE_CHOICES, default=SCOPE_READ)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.scope})"
