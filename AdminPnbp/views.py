from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.response import Response

from AdminMenu.auth_helpers import get_user_role_names, log_activity
from common.const.role_map import ROLE_KEYS, has_role

from .models import PnbpRate
from .serializers import PnbpRateSerializer
from .services import PnbpConfigurationError, validate_active_rate_set


EDITABLE_FIELDS = (
    "code_key",
    "label",
    "min_amount",
    "max_amount",
    "min_inclusive",
    "fee",
    "is_active",
    "sort_order",
)


def _is_admin(user):
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser or user.username.lower() == "admin":
        return True
    roles = list(get_user_role_names(user))
    return has_role(roles, ROLE_KEYS["ADMIN"]) or has_role(roles, ROLE_KEYS["SUPERADMIN"])


def _forbidden(request, api=False):
    if api:
        return Response({"detail": "Forbidden"}, status=403)
    return render(request, "403.html", status=403)


@login_required(login_url="")
def pnbp_config_page(request):
    if not _is_admin(request.user):
        return _forbidden(request)
    return render(request, "content/page-pnbp-config.html")


def _validate_projected_rates(changed_rate=None, new_rate=None):
    rates = list(PnbpRate.objects.all())
    if changed_rate is not None:
        rates = [changed_rate if rate.pk == changed_rate.pk else rate for rate in rates]
    if new_rate is not None:
        rates.append(new_rate)
    validate_active_rate_set(rates)


def _bulk_save_rates(request):
    payloads = request.data.get("rates") if isinstance(request.data, dict) else request.data
    if not isinstance(payloads, list) or not payloads:
        return Response({"rates": ["Konfigurasi PNBP harus berupa daftar yang tidak kosong."]}, status=400)

    with transaction.atomic():
        existing = list(PnbpRate.objects.select_for_update().all())
        before = PnbpRateSerializer(existing, many=True).data
        existing_by_id = {rate.id: rate for rate in existing}
        supplied_ids = {item.get("id") for item in payloads if item.get("id") is not None}
        if supplied_ids != set(existing_by_id):
            return Response(
                {"rates": ["Seluruh konfigurasi existing harus dikirim saat menyimpan perubahan."]},
                status=400,
            )

        candidates = []
        serializers = []
        for item in payloads:
            rate_id = item.get("id")
            instance = existing_by_id.get(rate_id) if rate_id is not None else None
            serializer = PnbpRateSerializer(
                instance,
                data=item,
                context={"skip_database_overlap": True},
            )
            serializer.is_valid(raise_exception=True)

            candidate = PnbpRate(pk=rate_id)
            if instance:
                candidate.created_by = instance.created_by
                candidate.created_at = instance.created_at
            for field in EDITABLE_FIELDS:
                setattr(candidate, field, serializer.validated_data[field])
            candidate.updated_by = request.user
            candidate.updated_at = timezone.now()
            if not instance:
                candidate.created_by = request.user
            candidates.append(candidate)
            serializers.append(serializer)

        code_keys = [candidate.code_key for candidate in candidates]
        if len(code_keys) != len(set(code_keys)):
            return Response({"code_key": ["Kode PNBP tidak boleh duplikat."]}, status=400)

        try:
            validate_active_rate_set(candidates)
        except PnbpConfigurationError as exc:
            return Response({"rates": exc.messages}, status=400)

        to_update = [candidate for candidate in candidates if candidate.pk]
        to_create = [candidate for candidate in candidates if not candidate.pk]
        if to_update:
            PnbpRate.objects.bulk_update(
                to_update,
                [*EDITABLE_FIELDS, "updated_by", "updated_at"],
            )
        if to_create:
            PnbpRate.objects.bulk_create(to_create)

        refreshed = list(
            PnbpRate.objects.select_related("created_by", "updated_by").all()
        )
        after = PnbpRateSerializer(refreshed, many=True).data
        transaction.on_commit(
            lambda: log_activity(
                request,
                "PNBP Configuration",
                "updated",
                "PnbpRate",
                0,
                {"before": before, "after": after},
            )
        )
    return Response(after)


@api_view(["GET", "POST", "PUT"])
def pnbp_rate_list(request):
    if not _is_admin(request.user):
        return _forbidden(request, api=True)

    if request.method == "GET":
        rates = PnbpRate.objects.select_related("created_by", "updated_by").all()
        return Response(PnbpRateSerializer(rates, many=True).data)

    if request.method == "PUT":
        return _bulk_save_rates(request)

    serializer = PnbpRateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    try:
        _validate_projected_rates(new_rate=PnbpRate(**serializer.validated_data))
    except PnbpConfigurationError as exc:
        return Response({"rates": exc.messages}, status=400)
    with transaction.atomic():
        rate = serializer.save(created_by=request.user, updated_by=request.user)
        transaction.on_commit(
            lambda: log_activity(
                request,
                "PNBP Configuration",
                "created",
                "PnbpRate",
                rate.id,
                {"after": PnbpRateSerializer(rate).data},
            )
        )
    return Response(PnbpRateSerializer(rate).data, status=201)


def _delete_rate(request, pk):
    with transaction.atomic():
        rates = list(PnbpRate.objects.select_for_update().all())
        rate = next((item for item in rates if item.pk == pk), None)
        if rate is None:
            return Response({"detail": "Konfigurasi PNBP tidak ditemukan."}, status=404)

        before = PnbpRateSerializer(rate).data
        remaining = [item for item in rates if item.pk != pk]
        adjusted = []

        if rate.is_active:
            active = sorted(
                (item for item in rates if item.is_active),
                key=lambda item: (item.min_amount, item.sort_order, item.pk),
            )
            if len(active) == 1:
                return Response(
                    {"rates": ["Minimal satu rentang PNBP harus tetap aktif."]},
                    status=400,
                )

            index = next(i for i, item in enumerate(active) if item.pk == pk)
            now = timezone.now()
            if index == 0:
                following = active[index + 1]
                following.min_amount = rate.min_amount
                following.min_inclusive = rate.min_inclusive
                following.updated_by = request.user
                following.updated_at = now
                adjusted.append(following)
            else:
                previous = active[index - 1]
                previous.max_amount = rate.max_amount
                previous.updated_by = request.user
                previous.updated_at = now
                adjusted.append(previous)

        try:
            validate_active_rate_set(remaining)
        except PnbpConfigurationError as exc:
            return Response({"rates": exc.messages}, status=400)

        if adjusted:
            PnbpRate.objects.bulk_update(
                adjusted,
                (
                    "min_amount",
                    "max_amount",
                    "min_inclusive",
                    "updated_by",
                    "updated_at",
                ),
            )

        deleted_id = rate.pk
        rate.delete()
        after = PnbpRateSerializer(
            PnbpRate.objects.select_related("created_by", "updated_by").all(),
            many=True,
        ).data
        transaction.on_commit(
            lambda: log_activity(
                request,
                "PNBP Configuration",
                "deleted",
                "PnbpRate",
                deleted_id,
                {"before": before, "after": after},
            )
        )
    return Response(status=204)


@api_view(["GET", "PUT", "PATCH", "DELETE"])
def pnbp_rate_detail(request, pk):
    if not _is_admin(request.user):
        return _forbidden(request, api=True)

    if request.method == "DELETE":
        return _delete_rate(request, pk)

    rate = get_object_or_404(PnbpRate, pk=pk)
    if request.method == "GET":
        return Response(PnbpRateSerializer(rate).data)

    before = PnbpRateSerializer(rate).data
    serializer = PnbpRateSerializer(
        rate,
        data=request.data,
        partial=request.method == "PATCH",
    )
    serializer.is_valid(raise_exception=True)
    projected = PnbpRate(pk=rate.pk)
    for field in EDITABLE_FIELDS:
        setattr(
            projected,
            field,
            serializer.validated_data.get(field, getattr(rate, field)),
        )
    try:
        _validate_projected_rates(changed_rate=projected)
    except PnbpConfigurationError as exc:
        return Response({"rates": exc.messages}, status=400)
    with transaction.atomic():
        rate = serializer.save(updated_by=request.user)
        transaction.on_commit(
            lambda: log_activity(
                request,
                "PNBP Configuration",
                "updated",
                "PnbpRate",
                rate.id,
                {"before": before, "after": PnbpRateSerializer(rate).data},
            )
        )
    return Response(PnbpRateSerializer(rate).data)
