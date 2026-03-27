import secrets


def ensure_unique_slug(instance, field_name: str, max_length: int = 48) -> None:
    """Assign a random URL-safe slug on *instance* if *field_name* is empty; must be unique for the model."""
    if getattr(instance, field_name, None):
        return
    model_cls = instance.__class__
    for _ in range(32):
        candidate = secrets.token_urlsafe(18)
        if len(candidate) > max_length:
            candidate = candidate[:max_length]
        qs = model_cls.objects.filter(**{field_name: candidate})
        if instance.pk:
            qs = qs.exclude(pk=instance.pk)
        if not qs.exists():
            setattr(instance, field_name, candidate)
            return
    setattr(instance, field_name, secrets.token_urlsafe(32)[:max_length])
