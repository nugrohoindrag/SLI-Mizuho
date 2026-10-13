from django.core.management.base import BaseCommand
from django.db import transaction

from AdminUser.models import User, UserBranch


class Command(BaseCommand):
    help = "Backfill staff_cabang user_branches from legacy users.branch_code."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show how many rows would be created without writing data.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        users = (
            User.objects
            .filter(roles__name__iexact="staff_cabang", is_superuser=False)
            .exclude(roles__name__iexact="HO")
            .exclude(branch__isnull=True)
            .select_related("branch")
            .only("id", "branch")
            .distinct()
            .order_by("id")
        )

        existing_pairs = set(
            UserBranch.objects.values_list("user_id", "branch_id")
        )

        rows = []
        for user in users:
            branch_code = getattr(user, "branch_id", None)
            if not branch_code:
                continue
            key = (user.id, branch_code)
            if key in existing_pairs:
                continue
            rows.append(UserBranch(user_id=user.id, branch_id=branch_code))

        if dry_run:
            self.stdout.write(
                self.style.WARNING(f"Dry run: {len(rows)} user_branches rows would be created.")
            )
            return

        with transaction.atomic():
            UserBranch.objects.bulk_create(rows, ignore_conflicts=True, batch_size=1000)

        self.stdout.write(
            self.style.SUCCESS(f"Created {len(rows)} user_branches rows from legacy users.branch_code.")
        )
