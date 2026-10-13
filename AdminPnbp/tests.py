from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from .models import PnbpRate
from .services import PnbpRateResolver, apply_dynamic_pnbp, validate_active_rate_set
from .views import _is_admin


class PnbpRateIntervalTests(SimpleTestCase):
    def rate(self, minimum, maximum, minimum_inclusive):
        return PnbpRate(
            code_key="TEST",
            label="Test",
            min_amount=Decimal(str(minimum)),
            max_amount=Decimal(str(maximum)) if maximum is not None else None,
            min_inclusive=minimum_inclusive,
            fee=Decimal("50000"),
        )

    def test_adjacent_ranges_do_not_overlap_when_next_lower_bound_is_exclusive(self):
        lower = self.rate(0, 50_000_000, True)
        upper = self.rate(50_000_000, 100_000_000, False)
        self.assertFalse(lower._overlaps(upper))
        self.assertFalse(upper._overlaps(lower))

    def test_adjacent_ranges_overlap_when_both_include_boundary(self):
        lower = self.rate(0, 50_000_000, True)
        upper = self.rate(50_000_000, 100_000_000, True)
        self.assertTrue(lower._overlaps(upper))

    def test_open_ended_range_overlaps_higher_range(self):
        open_ended = self.rate(1_000_000_000_000, None, False)
        higher = self.rate(2_000_000_000_000, 3_000_000_000_000, True)
        self.assertTrue(open_ended._overlaps(higher))


class PnbpRateResolverTests(SimpleTestCase):
    def setUp(self):
        self.rates = [
            PnbpRate(
                code_key="PNPB1",
                label="0 - 50jt",
                min_amount=Decimal("0"),
                max_amount=Decimal("50000000"),
                min_inclusive=True,
                fee=Decimal("50000"),
                sort_order=1,
            ),
            PnbpRate(
                code_key="PNPB2",
                label="50jt - 100jt",
                min_amount=Decimal("50000000"),
                max_amount=Decimal("100000000"),
                min_inclusive=False,
                fee=Decimal("100000"),
                sort_order=2,
            ),
            PnbpRate(
                code_key="PNPB3",
                label="> 100jt",
                min_amount=Decimal("100000000"),
                max_amount=None,
                min_inclusive=False,
                fee=Decimal("200000"),
                sort_order=3,
            ),
        ]
        self.resolver = PnbpRateResolver(self.rates)

    def test_exact_boundary_uses_lower_range(self):
        self.assertEqual(self.resolver.fee_for(50_000_000), Decimal("50000"))
        self.assertEqual(self.resolver.fee_for(100_000_000), Decimal("100000"))

    def test_value_above_boundary_uses_next_range(self):
        self.assertEqual(self.resolver.fee_for(50_000_001), Decimal("100000"))
        self.assertEqual(self.resolver.fee_for(100_000_001), Decimal("200000"))

    def test_missing_guarantee_value_returns_none(self):
        self.assertIsNone(self.resolver.fee_for(None))

    def test_gap_is_rejected(self):
        self.rates[1].min_amount = Decimal("60000000")
        with self.assertRaises(ValidationError):
            validate_active_rate_set(self.rates)

    def test_last_range_must_be_open_ended(self):
        self.rates[-1].max_amount = Decimal("200000000")
        with self.assertRaises(ValidationError):
            validate_active_rate_set(self.rates)

    def test_dynamic_fee_is_applied_only_when_st_is_used(self):
        active = SimpleNamespace(
            is_st_used=1,
            guarantee_value=Decimal("75000000"),
            pnbp_fee="999999",
        )
        inactive = SimpleNamespace(
            is_st_used=0,
            guarantee_value=Decimal("75000000"),
            pnbp_fee="999999",
        )

        self.assertTrue(apply_dynamic_pnbp(active, resolver=self.resolver))
        self.assertEqual(active.pnbp_fee, "100000")
        self.assertFalse(apply_dynamic_pnbp(inactive, resolver=self.resolver))
        self.assertEqual(inactive.pnbp_fee, "999999")


class PnbpAdminAccessTests(SimpleTestCase):
    def user(self, **overrides):
        values = {
            "is_authenticated": True,
            "is_superuser": False,
            "username": "user",
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_superuser_is_allowed(self):
        self.assertTrue(_is_admin(self.user(is_superuser=True)))

    @patch("AdminPnbp.views.get_user_role_names", return_value=["administrator"])
    def test_administrator_role_is_allowed(self, _roles):
        self.assertTrue(_is_admin(self.user()))

    @patch("AdminPnbp.views.get_user_role_names", return_value=["operator"])
    def test_non_admin_role_is_rejected(self, _roles):
        self.assertFalse(_is_admin(self.user()))
