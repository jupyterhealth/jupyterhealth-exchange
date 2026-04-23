from django.db import models
from jsonschema import ValidationError


class JheSetting(models.Model):
    JHE_SETTING_VALUE_TYPES = {
        "string": "string",
        "int": "int",
        "bool": "bool",
        "float": "float",
        "json": "json",
    }

    key = models.CharField(null=False, blank=False)

    setting_id = models.IntegerField(null=True, blank=True)

    value_type = models.CharField(max_length=10, choices=list(JHE_SETTING_VALUE_TYPES.items()))

    value_string = models.TextField(null=True, blank=True)
    value_int = models.IntegerField(null=True, blank=True)
    value_bool = models.BooleanField(null=True, blank=True)
    value_float = models.FloatField(null=True, blank=True)
    value_json = models.JSONField(null=True, blank=True)

    last_updated = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["key", "setting_id"],
                name="core_jhesetting_unique_key_setting_id",
            )
        ]

    def get_value(self):
        return {
            "string": self.value_string,
            "int": self.value_int,
            "bool": self.value_bool,
            "float": self.value_float,
            "json": self.value_json,
        }[self.value_type]

    def set_value(self, value_type: str, value):
        self.value_type = value_type

        # clear all typed columns first (important)
        self.value_string = None
        self.value_int = None
        self.value_bool = None
        self.value_float = None
        self.value_json = None

        if value_type == "string":
            self.value_string = "" if value is None else str(value)

        elif value_type == "int":
            try:
                self.value_int = int(value)
            except (TypeError, ValueError):
                raise ValidationError({"value": "Invalid int"})

        elif value_type == "bool":
            # Accept booleans or common strings
            if isinstance(value, bool):
                self.value_bool = value
            elif isinstance(value, str):
                v = value.strip().lower()
                if v in ("true", "1", "yes", "y", "on"):
                    self.value_bool = True
                elif v in ("false", "0", "no", "n", "off"):
                    self.value_bool = False
                else:
                    raise ValidationError({"value": "Invalid bool"})
            elif isinstance(value, (int, float)) and value in (0, 1):
                self.value_bool = bool(value)
            else:
                raise ValidationError({"value": "Invalid bool"})

        elif value_type == "float":
            try:
                self.value_float = float(value)
            except (TypeError, ValueError):
                raise ValidationError({"value": "Invalid float"})

        elif value_type == "json":
            # DRF will usually give you dict/list already
            self.value_json = value

        else:
            raise ValidationError({"value_type": "Unknown value_type"})
