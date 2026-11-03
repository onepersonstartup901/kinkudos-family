import logging

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from economy.lottery import send_due_lottery_reminders
from economy.models import FamilySettings
from economy.services import run_due_assignment_presets, send_due_assigned_task_nudges
from economy.weekly import pay_weekly_savings_interest, pay_weekly_streak_bonuses

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Run due weekly lottery reminders, soft assigned-task nudges, "
        "and saved assignment presets. Each step is isolated so one failure "
        "does not skip the others."
    )

    def handle(self, *args, **options):
        timezone.activate(FamilySettings.load().timezone_name or settings.TIME_ZONE)
        failures = []
        try:
            lottery_sent = self._run_step(
                "lottery reminders",
                send_due_lottery_reminders,
                failures,
            )
            nudge_sent = self._run_step(
                "assigned-task nudges",
                send_due_assigned_task_nudges,
                failures,
            )
            preset_batches = self._run_step(
                "assignment presets",
                run_due_assignment_presets,
                failures,
            )
            interest_paid = self._run_step(
                "weekly savings interest",
                pay_weekly_savings_interest,
                failures,
            )
            streaks_paid = self._run_step(
                "weekly streak bonuses",
                pay_weekly_streak_bonuses,
                failures,
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f"Sent {len(lottery_sent)} lottery reminder(s), "
                    f"{len(nudge_sent)} assigned-task nudge(s), "
                    f"created {len(preset_batches)} preset assignment(s), "
                    f"paid {len(interest_paid)} interest payout(s), "
                    f"and paid {len(streaks_paid)} streak bonus(es)."
                )
            )
            if failures:
                details = "; ".join(f"{name}: {exc}" for name, exc in failures)
                raise SystemExit(
                    f"Scheduled reminder step(s) failed: {details}"
                )
        finally:
            timezone.deactivate()

    def _run_step(self, label, callback, failures):
        try:
            return callback()
        except Exception as exc:
            logger.exception("Scheduled reminder step failed: %s", label)
            failures.append((label, exc))
            return []
