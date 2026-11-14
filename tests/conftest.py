"""Run command tests on an isolated, resettable copy of official Sakila."""

import os
import subprocess
import sys

import pytest
from sqlalchemy import delete, inspect, update
from sqlalchemy.orm import Session

from sakila_sync import source as s
from sakila_sync.config import analytics_engine, mysql_engine, mysql_url

NEW_RENTAL_ID = 60000
NEW_PAYMENT_IDS = (60000, 60001)
RETURNED_RENTAL_ID = 11496
MOVED_CUSTOMER_ID = 97
RENAMED_COUNTRY_ID = 8
RELATED_STORE_ID = 2


@pytest.fixture
def databases(tmp_path, monkeypatch):
    clean_url = mysql_url()
    test_name = os.environ.get("SAKILA_TEST_DATABASE", "sakila_test")
    if not test_name.endswith("_test") or clean_url.database == test_name:
        pytest.fail("Use a separate test schema ending in _test.")
    clean = mysql_engine(clean_url)
    test = mysql_engine(clean_url.set(database=test_name))
    target_path = tmp_path / "analytics.sqlite"
    target = analytics_engine(target_path)

    def reset():
        with Session(clean) as original, Session(test) as copy:
            with original.begin(), copy.begin():
                if original.get(s.Rental, NEW_RENTAL_ID) or any(
                    original.get(s.Payment, payment_id) for payment_id in NEW_PAYMENT_IDS
                ):
                    pytest.fail("The clean Sakila fixture already uses the reserved test IDs.")
                copy.execute(delete(s.Payment).where(s.Payment.payment_id.in_(NEW_PAYMENT_IDS)))
                copy.execute(delete(s.Rental).where(s.Rental.rental_id == NEW_RENTAL_ID))
                for model, key in (
                    (s.Rental, RETURNED_RENTAL_ID),
                    (s.Customer, MOVED_CUSTOMER_ID),
                    (s.Country, RENAMED_COUNTRY_ID),
                    (s.Store, RELATED_STORE_ID),
                ):
                    original_row = original.get(model, key)
                    if original_row is None or copy.get(model, key) is None:
                        pytest.fail(f"Load official Sakila into both schemas. Missing {model.__name__} {key}.")
                    pk = inspect(model).primary_key[0].key
                    values = {
                        col.key: getattr(original_row, col.key)
                        for col in inspect(model).column_attrs if col.key != pk
                    }
                    copy.execute(update(model).where(getattr(model, pk) == key).values(**values))

    try:
        reset()
        monkeypatch.setenv("SAKILA_DATABASE", test_name)
        monkeypatch.setenv("ANALYTICS_DB", str(target_path))
        yield test, target
    finally:
        try:
            reset()
        finally:
            for engine in (target, test, clean):
                engine.dispose()


@pytest.fixture
def command(databases):
    def run(*args, expected=0):
        result = subprocess.run(
            [sys.executable, "-m", "sakila_sync", *args],
            capture_output=True, text=True, timeout=90, check=False,
        )
        output = result.stdout + result.stderr
        print(f"$ python -m sakila_sync {' '.join(args)}")
        print(output.rstrip())
        print(f"Exit code {result.returncode}")
        assert result.returncode == expected, output
        return output

    return run
