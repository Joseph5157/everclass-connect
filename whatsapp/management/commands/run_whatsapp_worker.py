import logging
import signal
import time

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import close_old_connections

from whatsapp import worker

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Send queued messages and process stored webhook events (runs forever; Railway worker service)."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Run one pass and exit.")

    def handle(self, *args, once=False, **options):
        stopping = False

        def stop(signum, frame):
            nonlocal stopping
            stopping = True
            logger.info("Stopping after current pass…")

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        logger.info("WhatsApp worker started")

        while not stopping:
            close_old_connections()
            try:
                handled = worker.run_once()
            except Exception:
                logger.exception("Worker pass failed")
                handled = 0
            if once:
                self.stdout.write(f"Handled {handled} item(s).")
                return
            if not handled:
                time.sleep(settings.WORKER_POLL_SECONDS)
