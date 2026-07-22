import os
import uuid
import datetime

from django.db import models


class DateModel(models.Model):
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


def get_unique_filename(filename, subfolder):
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    unique_id = uuid.uuid4().hex
    extension = filename.split('.')[-1]
    return os.path.join(subfolder, f"{timestamp}_{unique_id}.{extension}")
