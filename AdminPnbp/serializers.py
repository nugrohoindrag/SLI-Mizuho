from rest_framework import serializers

from .models import PnbpRate


class PnbpRateSerializer(serializers.ModelSerializer):
    created_by_name = serializers.CharField(source="created_by.username", read_only=True)
    updated_by_name = serializers.CharField(source="updated_by.username", read_only=True)

    class Meta:
        model = PnbpRate
        fields = (
            "id",
            "code_key",
            "label",
            "min_amount",
            "max_amount",
            "min_inclusive",
            "fee",
            "is_active",
            "sort_order",
            "created_by_name",
            "updated_by_name",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("created_at", "updated_at")

    def validate(self, attrs):
        candidate = self.instance or PnbpRate()
        for field, value in attrs.items():
            setattr(candidate, field, value)
        if self.context.get("skip_database_overlap"):
            candidate.clean_configuration_fields()
        else:
            candidate.clean()
        attrs["code_key"] = candidate.code_key
        attrs["label"] = candidate.label
        return attrs
