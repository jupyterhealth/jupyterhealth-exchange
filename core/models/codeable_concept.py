from django.db import models


class CodeableConcept(models.Model):
    coding_system = models.CharField()
    coding_code = models.CharField()
    text = models.CharField()

    def __str__(self):
        return self.text or self.coding_code

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["coding_system", "coding_code"],
                name="core_codeableconcept_coding_system_coding_code",
            )
        ]
