"""Database engines configured through the environment."""

import os
from pathlib import Path

from sqlalchemy import URL, create_engine, event


def mysql_url(prefix="SAKILA_"):
    settings = os.environ
    return URL.create(
        "mysql+pymysql",
        username=settings[prefix + "USER"],
        password=settings.get(prefix + "PASSWORD"),
        host=settings.get(prefix + "HOST", "127.0.0.1"),
        port=int(settings.get(prefix + "PORT", "3306")),
        database=settings.get(prefix + "DATABASE", "sakila"),
        query={
            "charset": "utf8mb4",
            **(
                {"unix_socket": settings[prefix + "SOCKET"]}
                if settings.get(prefix + "SOCKET")
                else {}
            ),
        },
    )


def mysql_engine(url):
    return create_engine(
        url,
        isolation_level="REPEATABLE READ",
        connect_args={"init_command": "SET time_zone = '+00:00'"},
    )


def analytics_engine(path):
    engine = create_engine(
        URL.create("sqlite", database=str(Path(path).expanduser().resolve())),
        connect_args={"autocommit": False},
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _):
        previous = connection.autocommit
        connection.autocommit = True
        try:
            cursor = connection.cursor()
            try:
                cursor.execute("PRAGMA foreign_keys=ON")
            finally:
                cursor.close()
        finally:
            connection.autocommit = previous

    return engine
