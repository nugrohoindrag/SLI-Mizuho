from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from AdminUser.authentication import UserSerializer
from AdminUser.branch_access import (
    get_user_branch_codes,
    require_user_branches,
    user_is_branch_restricted,
)
from AktaSertifikat.views import _user_can_access_branch


class BranchAccessRolePrecedenceTests(SimpleTestCase):
    def _user(self, *, superuser=False):
        return SimpleNamespace(is_superuser=superuser, is_authenticated=True)

    @patch("AdminUser.branch_access.get_user_role_names")
    def test_staff_cabang_only_is_branch_restricted(self, role_names):
        role_names.return_value = ["staff_cabang"]

        self.assertTrue(user_is_branch_restricted(self._user()))

    @patch("AdminUser.branch_access.get_user_role_names")
    def test_only_ho_takes_precedence_over_staff_role(self, role_names):
        role_names.return_value = ["staff_cabang", "HO"]
        self.assertFalse(user_is_branch_restricted(self._user()))

        for other_role in ("superadministrator", "operator", "Warkah"):
            with self.subTest(other_role=other_role):
                role_names.return_value = ["staff_cabang", other_role]
                self.assertTrue(user_is_branch_restricted(self._user()))

    @patch("AdminUser.branch_access.get_user_role_names")
    def test_global_role_does_not_require_branch_assignment(self, role_names):
        role_names.return_value = ["operator"]

        branch_codes, error = require_user_branches(self._user())

        self.assertEqual(branch_codes, [])
        self.assertIsNone(error)

    @patch("AdminUser.branch_access.get_primary_user_branch_code", return_value="LEGACY")
    @patch("AdminUser.branch_access.UserBranch.objects")
    @patch("AdminUser.branch_access.get_user_role_names", return_value=["staff_cabang"])
    def test_staff_uses_only_user_branch_assignments(
        self, _role_names, user_branch_objects, _primary_branch
    ):
        values = MagicMock()
        values.distinct.return_value = ["001", "002"]
        user_branch_objects.filter.return_value.values_list.return_value = values

        self.assertEqual(get_user_branch_codes(self._user()), ["001", "002"])

    @patch("AdminUser.branch_access.get_primary_user_branch_code", return_value="LEGACY")
    @patch("AdminUser.branch_access.UserBranch.objects")
    @patch("AdminUser.branch_access.get_user_role_names", return_value=["operator"])
    def test_other_roles_keep_only_legacy_primary_branch(
        self, _role_names, user_branch_objects, _primary_branch
    ):
        self.assertEqual(get_user_branch_codes(self._user()), ["LEGACY"])
        user_branch_objects.filter.assert_not_called()


class DownloadBranchAccessTests(SimpleTestCase):
    @patch("AktaSertifikat.views.get_user_branch_codes")
    @patch("AktaSertifikat.views._user_is_staff_cabang", return_value=True)
    def test_staff_cabang_can_download_from_any_assigned_branch(
        self, _is_staff_cabang, branch_codes
    ):
        branch_codes.return_value = ["001", "002"]

        self.assertTrue(_user_can_access_branch(self._user(), "002"))
        self.assertFalse(_user_can_access_branch(self._user(), "003"))

    @patch("AktaSertifikat.views._user_is_staff_cabang", return_value=False)
    def test_global_role_can_download_without_branch_assignment(self, _is_staff_cabang):
        self.assertTrue(_user_can_access_branch(self._user(), "999"))

    @staticmethod
    def _user():
        return SimpleNamespace(is_authenticated=True)


class UserSerializerBranchRoleTests(SimpleTestCase):
    def setUp(self):
        self.serializer = UserSerializer()

    def test_only_staff_cabang_receives_multi_branch_assignments(self):
        staff = [SimpleNamespace(name="staff_cabang")]
        staff_and_ho = [
            SimpleNamespace(name="staff_cabang"),
            SimpleNamespace(name="HO"),
        ]
        operator = [SimpleNamespace(name="operator")]

        self.assertTrue(self.serializer._is_staff_cabang_role(staff))
        self.assertFalse(self.serializer._is_staff_cabang_role(staff_and_ho))
        self.assertFalse(self.serializer._is_staff_cabang_role(operator))

    def test_branch_codes_are_normalized_without_duplicates(self):
        self.assertEqual(
            self.serializer._normalize_branch_codes([" 001 ", "002", "001"]),
            ["001", "002"],
        )
