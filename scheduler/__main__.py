import argparse
import datetime
import os
import sys
from pathlib import Path

import yaml

from .bluesky import publisher as bluesky
from .shared import database
from .shared.calendars import website
from .shared.events import load_events
from .shared.ledger import LedgerError, locked

PLATFORMS = {"bluesky": bluesky}


def main():
    parser = argparse.ArgumentParser(prog="scheduler", description="Post upcoming blr.today events to social networks")
    parser.add_argument("--config", default=os.environ.get("SCHEDULER_CONFIG", "scheduler.yml"))
    parser.add_argument("--state", default=os.environ.get("SCHEDULER_STATE", "state"), help="Directory for events.db and the ledgers")
    parser.add_argument("--website", help="Local blr-today/website checkout for calendar definitions")
    parser.add_argument("--dry-run", action="store_true", help="Read real state, print writes, change nothing")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("run", help="Post whatever is due (the default)")
    adopt = sub.add_parser("adopt", help="Build a missing ledger from the account's history")
    adopt.add_argument("platform", choices=sorted(PLATFORMS))
    args = parser.parse_args()

    config = yaml.safe_load(Path(args.config).read_text())
    now = datetime.datetime.now(datetime.UTC)
    try:
        with locked(args.state):
            if args.command == "adopt":
                PLATFORMS[args.platform].run(config[args.platform], [], args.state, now, None, args.dry_run, adopting=True)
                return 0
            db = database.fetch(config["database"], Path(args.state, "events.db"), now)
            events = list(load_events(db))
            get = website(args.website)
            failed = False
            for name, platform in PLATFORMS.items():
                if name not in config:
                    continue
                try:
                    platform.run(config[name], events, args.state, now, get, args.dry_run)
                except LedgerError:
                    raise
                except Exception as e:
                    print(f"{name}: failed: {e!r}", file=sys.stderr)
                    failed = True
            return 1 if failed else 0
    except (LedgerError, database.StaleDatabase) as e:
        # Refusing to run is the safe outcome, and it needs a human
        print(f"refusing to run: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
