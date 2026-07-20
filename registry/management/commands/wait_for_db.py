import time
import logging
from django.core.management.base import BaseCommand
from django.db import connections
from django.db.utils import OperationalError

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Waits until the database is ready to accept connections.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--max-retries',
            type=int,
            default=30,
            help='Maximum number of connection attempts before giving up (default: 30).',
        )
        parser.add_argument(
            '--interval',
            type=float,
            default=3.0,
            help='Seconds to wait between retries (default: 3).',
        )

    def handle(self, *args, **options):
        max_retries = options['max_retries']
        interval = options['interval']
        db_conn = connections['default']

        self.stdout.write('Waiting for database...')
        for attempt in range(1, max_retries + 1):
            try:
                db_conn.ensure_connection()
                self.stdout.write(self.style.SUCCESS(
                    f'Database ready after {attempt} attempt(s).'
                ))
                return
            except OperationalError as e:
                self.stdout.write(
                    f'  Attempt {attempt}/{max_retries}: DB not ready — {e}. '
                    f'Retrying in {interval}s...'
                )
                time.sleep(interval)

        self.stderr.write(self.style.ERROR(
            f'Database did not become ready after {max_retries} attempts. Aborting.'
        ))
        raise SystemExit(1)
