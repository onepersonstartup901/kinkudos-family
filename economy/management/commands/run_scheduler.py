"""Built-in scheduler for hosts without systemd timers (e.g. Hostinger Docker).

Runs run_scheduled_reminders every 15 minutes (daily job lists, nudges,
weekly interest and streak bonuses) and run_maintenance once a day.
"""

import logging
import time
from datetime import timedelta

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.utils import timezone

logger = logging.getLogger(__name__)

REMINDER_INTERVAL = timedelta(minutes=15)
MAINTENANCE_INTERVAL = timedelta(hours=24)


class Command(BaseCommand):
    help = "Run KinKudos scheduled jobs in a loop (for hosts without systemd timers)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--once",
            action="store_true",
            help="Run every job once and exit.",
        )

    def handle(self, *args, **options):
        last_maintenance = None
        while True:
            started = timezone.now()
            self._run("run_scheduled_reminders")
            if last_maintenance is None or started - last_maintenance >= MAINTENANCE_INTERVAL:
                self._run("run_maintenance")
                last_maintenance = started
            if options["once"]:
                return
            elapsed = (timezone.now() - started).total_seconds()
            time.sleep(max(REMINDER_INTERVAL.total_seconds() - elapsed, 30))

    def _run(self, command):
        try:
            call_command(command, stdout=self.stdout, stderr=self.stderr)
        except (Exception, SystemExit):
            # One failing step must not stop the scheduler; the next tick retries.
            logger.exception("Scheduled command failed: %s", command)
