#!/usr/bin/env python
import os
import sys
from pathlib import Path


def load_local_env():
    """Load .env for local dev only. Real environment variables always win; production has no .env file."""
    env_file = Path(__file__).resolve().parent / ".env"
    if env_file.is_file():
        from dotenv import load_dotenv

        load_dotenv(env_file, override=False)


def main():
    load_local_env()
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "connect.settings")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
