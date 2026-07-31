from django.db import models


class EhrBrand(models.Model):
    """
    A patient-recognizable EHR organization/brand (SMART App Launch 2.2
    "user-access brand"). Vendor-neutral: seeded from Epic today, but not
    Epic-only. fhir_base_url is the SMART `iss` the patient is sent to; the
    authorize/token endpoints are discovered from it via
    `{fhir_base_url}/.well-known/smart-configuration`.
    """

    name = models.TextField()
    vendor = models.CharField(default="epic")
    fhir_base_url = models.TextField(unique=True)
    fhir_version = models.CharField(null=True, blank=True)
    npi = models.CharField(null=True, blank=True)
    logo_url = models.TextField(null=True, blank=True)  # reserved; Epic brands file has no logo
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name or f"EhrBrand {self.pk}"


class EhrBrandLocation(models.Model):
    """
    A physical care facility under a brand -- the row a patient searches and
    picks. All locations of a brand share the brand's fhir_base_url.
    """

    brand = models.ForeignKey(EhrBrand, on_delete=models.CASCADE, related_name="locations")
    name = models.TextField()
    address_text = models.TextField(blank=True, default="")
    city = models.CharField(null=True, blank=True)
    state = models.CharField(null=True, blank=True)
    postal_code = models.CharField(null=True, blank=True)
    country = models.CharField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["state"]),
            models.Index(fields=["postal_code"]),
            models.Index(fields=["name"]),
        ]

    def __str__(self):
        return self.name or f"EhrBrandLocation {self.pk}"
