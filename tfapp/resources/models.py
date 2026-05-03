from django.conf import settings
from django.db import models
from django.utils.text import slugify


class EmployeeHandbook(models.Model):
    """Singleton row: current employee handbook PDF (replace via admin)."""

    pdf = models.FileField(upload_to="employee_handbook/")
    updated_at = models.DateTimeField(auto_now=True)
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        verbose_name = "Employee handbook (PDF)"
        verbose_name_plural = "Employee handbook (PDF)"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        pass

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return "Employee handbook"


class Policy(models.Model):
    title = models.CharField(max_length=300)
    slug = models.SlugField(max_length=320, unique=True, blank=True)
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["title"]
        verbose_name_plural = "Policies"

    def save(self, *args, **kwargs):
        base = slugify(self.title)[:300] or "policy"
        slug = base
        n = 0
        while True:
            qs = Policy.objects.filter(slug=slug)
            if self.pk:
                qs = qs.exclude(pk=self.pk)
            if not qs.exists():
                break
            n += 1
            slug = f"{base}-{n}"[:320]
        self.slug = slug
        super().save(*args, **kwargs)

    def __str__(self):
        return self.title


class ResourceEvent(models.Model):
    title = models.CharField(max_length=200, blank=True)
    event_date = models.DateField()
    event_time = models.TimeField(null=True, blank=True)
    all_day = models.BooleanField(default=False)
    details = models.TextField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="resource_events_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["event_date", "event_time", "pk"]

    def __str__(self):
        label = self.title or f"Event {self.event_date}"
        return label


class EventAttachment(models.Model):
    event = models.ForeignKey(
        ResourceEvent,
        on_delete=models.CASCADE,
        related_name="attachments",
    )
    image = models.ImageField(upload_to="resource_events/%Y/%m/")

    class Meta:
        ordering = ["pk"]

    def __str__(self):
        return f"Attachment for {self.event_id}"
