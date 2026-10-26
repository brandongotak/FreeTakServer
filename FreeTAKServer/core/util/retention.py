"""delete old records FTS would otherwise keep forever

    python -m FreeTAKServer.core.util.retention [--days N]

Run daily by the installer's fts-retention timer. It deletes REST API call
records (the APICalls table, which stores each call's content) older than
RetentionDays (FTS_RETENTION_DAYS, default 30) and drops expired quick-connect
enrollment codes. CoT is not kept as history (only the latest state), and log
files are rotated by logrotate."""
import argparse
import datetime

from sqlalchemy import create_engine, text

from FreeTAKServer.core.configuration.DatabaseConfiguration import DatabaseConfiguration
from FreeTAKServer.core.configuration.MainConfig import MainConfig
from FreeTAKServer.core.util import enroll_codes

config = MainConfig.instance()


def prune_api_calls(days: int) -> int:
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=days)
    engine = create_engine(DatabaseConfiguration().DataBaseConnectionString)
    with engine.begin() as connection:
        exists = connection.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='APICalls'")).first() \
            if engine.dialect.name == "sqlite" else True
        if not exists:
            return 0
        return connection.execute(text('DELETE FROM "APICalls" WHERE timestamp < :cutoff'), {"cutoff": cutoff}).rowcount


def main():
    parser = argparse.ArgumentParser(description="Delete old FTS records.")
    parser.add_argument("--days", type=int, default=config.RetentionDays)
    days = max(1, parser.parse_args().days)
    print(f"deleted {prune_api_calls(days)} API call records older than {days} days")
    print(f"dropped {enroll_codes.prune()} expired enrollment codes")


if __name__ == "__main__":
    main()
