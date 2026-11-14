"""Commands for initializing, synchronizing, and validating analytics."""

import argparse
import logging
import os
from datetime import date
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .config import analytics_engine, mysql_engine, mysql_url
from .sync import initialize, synchronize
from .validation import validate


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="create the analytics database and load source dates")
    commands.add_parser("full-load", help="load all Sakila reporting data")
    commands.add_parser("incremental", help="load new or changed Sakila data")
    validation = commands.add_parser("validate", help="compare MySQL and SQLite data")
    validation.add_argument("--from", dest="start", type=date.fromisoformat, metavar="YYYY-MM-DD")
    validation.add_argument("--to", dest="end", type=date.fromisoformat, metavar="YYYY-MM-DD")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logger = logging.getLogger("sakila_sync")

    source = target = None
    try:
        url = mysql_url()
        path = Path(os.environ.get("ANALYTICS_DB", "analytics.sqlite")).expanduser()
        if args.command != "init" and not path.is_file():
            raise ValueError("Run init to create the analytics database")
        source = mysql_engine(url)
        target = analytics_engine(path)
        if args.command == "init":
            message = initialize(source, target)
        elif args.command == "validate":
            with Session(source) as source_session, Session(target) as target_session:
                with source_session.begin(), target_session.begin():
                    message = validate(
                        source_session, target_session, args.start, args.end, use_default_period=True
                    )
        else:
            message = synchronize(source, target, full=args.command == "full-load")
        logger.info("%s succeeded. %s", args.command, message)
        return 0
    except KeyError as error:
        name = error.args[0]
        if name == "SAKILA_USER":
            logger.error("%s failed. Set SAKILA_USER in the environment.", args.command)
        else:
            logger.error("%s failed. Missing source or dimension key %s.", args.command, name)
    except SQLAlchemyError as error:
        logger.error("%s failed. %s", args.command, getattr(error, "orig", error))
    except (ValueError, OSError) as error:
        logger.error("%s failed. %s", args.command, error)
    finally:
        for engine in (source, target):
            if engine is not None:
                engine.dispose()
    return 1
