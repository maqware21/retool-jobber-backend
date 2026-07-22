from safedelete import SOFT_DELETE_CASCADE
from safedelete.models import SafeDeleteModel

from helpers.models import DateModel
from django.db import models


class Tenant(DateModel, SafeDeleteModel):
    """
    One row per VoltPro customer organisation.
    business_name is populated when the customer connects their Jobber account (Week 2).
    """
    _safedelete_policy = SOFT_DELETE_CASCADE

    business_name = models.CharField(max_length=255, null=True, blank=True)

    class Meta:
        db_table = 'tenants'
        verbose_name = 'tenant'
        verbose_name_plural = 'tenants'

    def __str__(self):
        return self.business_name or f"Tenant #{self.pk}"

    @classmethod
    def fetch(cls, tenant_id=None):
        try:
            data = cls.objects.filter(is_active=True)
            if tenant_id:
                data = data.filter(id=tenant_id).first()
            return data
        except Exception:
            return None
