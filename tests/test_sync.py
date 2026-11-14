"""Five command-level tests against official Sakila and a fresh SQLite file."""

from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import delete, func, inspect, select, update
from sqlalchemy.orm import Session

from sakila_sync import source as s
from sakila_sync import target as t

from conftest import (
    MOVED_CUSTOMER_ID, NEW_PAYMENT_IDS, NEW_RENTAL_ID,
    RELATED_STORE_ID, RENAMED_COUNTRY_ID, RETURNED_RENTAL_ID,
)


def load(command):
    command("init")
    command("full-load")


def test_init(databases, command):
    _, target = databases
    command("init")
    assert set(inspect(target).get_table_names()) == set(t.TargetBase.metadata.tables)
    with Session(target) as db:
        markers = db.scalars(select(t.SyncState)).all()
        assert {row.source_table for row in markers} == {model.__tablename__ for model in s.SOURCE_TABLES}
        assert all(not row.loaded and row.last_update is None for row in markers)
        day = db.get(t.DimDate, 20050706)
        assert (day.date, day.quarter, day.day_of_week, day.is_weekend) == (date(2005, 7, 6), 3, 3, 0)


def test_full_load(databases, command):
    source, target = databases
    load(command)
    with Session(target) as db:
        first_key = db.scalar(select(t.DimFilm.film_key).where(t.DimFilm.film_id == 141))
    command("full-load")
    with Session(source) as src, Session(target) as dst:
        for original, copy in (
            (s.Film, t.DimFilm), (s.Actor, t.DimActor), (s.Category, t.DimCategory),
            (s.Store, t.DimStore), (s.Customer, t.DimCustomer),
            (s.FilmActor, t.BridgeFilmActor), (s.FilmCategory, t.BridgeFilmCategory),
            (s.Rental, t.FactRental), (s.Payment, t.FactPayment),
        ):
            assert src.scalar(select(func.count()).select_from(original)) == dst.scalar(
                select(func.count()).select_from(copy)
            )
        film = dst.scalar(select(t.DimFilm).where(t.DimFilm.film_id == 141))
        assert (film.film_key, film.title, film.language) == (first_key, "CHICAGO NORTH", "English")
        rental = dst.scalar(select(t.FactRental).where(t.FactRental.rental_id == 3818))
        assert (rental.date_key_rented, rental.date_key_returned, rental.rental_duration_days) == (
            20050706, 20050711, 5,
        )
        assert all(row.loaded for row in dst.scalars(select(t.SyncState)))


def test_incremental_new(databases, command):
    source, target = databases
    load(command)
    with Session(target) as db:
        rental_marker = db.get(t.SyncState, "rental").last_update
        payment_marker = db.get(t.SyncState, "payment").last_update

    with Session(source) as db, db.begin():
        staff = db.scalar(select(s.Staff).order_by(s.Staff.staff_id).limit(1))
        inventory = db.scalar(
            select(s.Inventory).where(s.Inventory.store_id != staff.store_id)
            .order_by(s.Inventory.inventory_id).limit(1)
        )
        rental_store, fallback_store = inventory.store_id, staff.store_id
        db.add(s.Rental(
            rental_id=NEW_RENTAL_ID, rental_date=datetime(2006, 3, 4, 23, 30),
            inventory_id=inventory.inventory_id, customer_id=1, staff_id=staff.staff_id,
            last_update=rental_marker,
        ))
        db.flush()
        for payment_id, rental_id in zip(NEW_PAYMENT_IDS, (NEW_RENTAL_ID, None)):
            db.add(s.Payment(
                payment_id=payment_id, customer_id=1, staff_id=staff.staff_id,
                rental_id=rental_id, amount=Decimal("4.99"),
                payment_date=datetime(2006, 3, 5, 12), last_update=payment_marker,
            ))
        db.flush()
        db.execute(update(s.Rental).where(s.Rental.rental_id == NEW_RENTAL_ID).values(
            rental_date=datetime(2006, 3, 4, 23, 30), last_update=rental_marker,
        ))
        db.execute(update(s.Payment).where(s.Payment.payment_id.in_(NEW_PAYMENT_IDS)).values(
            payment_date=datetime(2006, 3, 5, 12), last_update=payment_marker,
        ))

    command("incremental")
    command("incremental")
    with Session(target) as db:
        rental = db.scalars(
            select(t.FactRental).where(t.FactRental.rental_id == NEW_RENTAL_ID)
        ).one()
        assert (rental.date_key_rented, rental.date_key_returned, rental.rental_duration_days) == (
            20060304, None, None,
        )
        assert db.get(t.DimDate, 20060304).day_of_week == 6
        assert db.get(t.DimDate, 20060305).is_weekend == 1
        for payment_id, expected_store in zip(NEW_PAYMENT_IDS, (rental_store, fallback_store)):
            payment = db.scalars(
                select(t.FactPayment).where(t.FactPayment.payment_id == payment_id)
            ).one()
            assert payment.date_key_paid == 20060305
            assert payment.amount == Decimal("4.99")
            assert db.get(t.DimStore, payment.store_key).store_id == expected_store


def test_incremental_updates(databases, command):
    source, target = databases
    with Session(source) as db, db.begin():
        db.get(s.Store, RELATED_STORE_ID).last_update -= timedelta(seconds=1)
    load(command)
    with Session(target) as db:
        rental = db.scalar(select(t.FactRental).where(t.FactRental.rental_id == RETURNED_RENTAL_ID))
        customer = db.scalar(select(t.DimCustomer).where(t.DimCustomer.customer_id == MOVED_CUSTOMER_ID))
        rental_key, customer_key = rental.fact_rental_key, customer.customer_key
        old_city = customer.city
        store_marker = db.get(t.SyncState, "store").last_update
        assert rental.date_key_returned is None

    with Session(source) as db, db.begin():
        rental = db.get(s.Rental, RETURNED_RENTAL_ID)
        rental.return_date = rental.rental_date + timedelta(days=3)
        expected_return = int(rental.return_date.strftime("%Y%m%d"))
        new_address, expected_city, expected_country = db.execute(
            select(s.Address.address_id, s.City.city, s.Country.country)
            .join(s.City, s.City.city_id == s.Address.city_id)
            .join(s.Country, s.Country.country_id == s.City.country_id)
            .where(s.City.city != old_city, s.Country.country_id != RENAMED_COUNTRY_ID)
            .order_by(s.Address.address_id).limit(1)
        ).one()
        db.get(s.Customer, MOVED_CUSTOMER_ID).address_id = new_address
        store = db.get(s.Store, RELATED_STORE_ID)
        assert store.last_update < store_marker
        city = db.get(s.City, db.get(s.Address, store.address_id).city_id)
        assert city.country_id == RENAMED_COUNTRY_ID
        db.get(s.Country, RENAMED_COUNTRY_ID).country = "Updated country"

    command("incremental")
    with Session(target) as db:
        rental = db.get(t.FactRental, rental_key)
        assert (rental.date_key_returned, rental.rental_duration_days) == (expected_return, 3)
        customer = db.get(t.DimCustomer, customer_key)
        assert (customer.city, customer.country) == (expected_city, expected_country)
        store = db.scalar(select(t.DimStore).where(t.DimStore.store_id == RELATED_STORE_ID))
        assert store.country == "Updated country"


def test_validate(databases, command):
    _, target = databases
    load(command)
    assert "2006-01-16 through 2006-02-14" in command("validate")
    period = ("validate", "--from", "2005-07-01", "--to", "2005-07-31")
    assert "2005-07-01 through 2005-07-31" in command(*period)

    with Session(target) as db, db.begin():
        payment = db.scalar(
            select(t.FactPayment).where(
                t.FactPayment.date_key_paid.between(20050701, 20050731)
            ).order_by(t.FactPayment.payment_id).limit(1)
        )
        payment_id = payment.payment_id
        payment.amount += Decimal("0.02")

    assert "payment total mismatch" in command(*period, expected=1)
    with Session(target) as db, db.begin():
        db.execute(delete(t.FactPayment).where(t.FactPayment.payment_id == payment_id))
    error = command(*period, expected=1)
    assert "payment count mismatch" in error and "1 missing" in error
