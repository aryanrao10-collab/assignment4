"""Read Sakila through mapped queries and upsert analytics rows in order."""

from datetime import date
from itertools import batched

from sqlalchemy import func, inspect, or_, select, tuple_
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from . import source as s
from . import target as t
from .validation import validate

BATCH_SIZE = 500


def rows(session, statement, key=None, ids=None):
    if ids is None:
        return session.execute(statement).all()
    result = []
    for batch in batched(sorted(ids), BATCH_SIZE):
        result.extend(session.execute(statement.where(key.in_(batch))).all())
    return result


def related_ids(session, key, foreign_key, ids, *conditions):
    statement = select(key).where(*conditions)
    return {value for (value,) in rows(session, statement, foreign_key, ids)}


def upsert(session, model, keys, records):
    for batch in batched(records, BATCH_SIZE):
        statement = insert(model)
        updated = {
            name: statement.excluded[name] for name in batch[0] if name not in keys
        }
        if updated:
            statement = statement.on_conflict_do_update(index_elements=keys, set_=updated)
        else:
            statement = statement.on_conflict_do_nothing(index_elements=keys)
        session.execute(statement, list(batch), execution_options={"render_nulls": True})


def date_key(day):
    return day.year * 10000 + day.month * 100 + day.day


def write_dates(target, dates):
    upsert(target, t.DimDate, ("date_key",), (
        {
            "date_key": date_key(day), "date": day, "year": day.year,
            "quarter": (day.month - 1) // 3 + 1, "month": day.month,
            "day_of_month": day.day, "day_of_week": day.isoweekday(),
            "is_weekend": int(day.isoweekday() >= 6),
        }
        for day in sorted(dates)
    ))


def initialize(source_engine, target_engine):
    with Session(source_engine) as source, Session(target_engine) as target:
        with source.begin(), target.begin():
            version = source.scalar(select(func.version()))
            dates = set()
            for timestamp in (s.Rental.rental_date, s.Rental.return_date, s.Payment.payment_date):
                for value in source.scalars(select(func.date(timestamp)).distinct()):
                    if value is not None:
                        dates.add(value if isinstance(value, date) else date.fromisoformat(value))
            t.TargetBase.metadata.create_all(target.connection())
            write_dates(target, dates)
            statement = insert(t.SyncState).on_conflict_do_nothing(index_elements=["source_table"])
            target.execute(statement, [
                {"source_table": model.__tablename__, "last_update": None, "loaded": False}
                for model in s.SOURCE_TABLES
            ])
    return f"MySQL {version} connected. SQLite tables and {len(dates)} source dates ready."


def scan_changes(source, state, full):
    changed = {}
    markers = []
    for model in s.SOURCE_TABLES:
        name = model.__tablename__
        previous = None if full else state[name].last_update
        columns = [getattr(model, col.key) for col in inspect(model).primary_key]
        if full or previous is None:
            newest = source.scalar(select(func.max(model.last_update)))
            changed[model] = None
        else:
            statement = select(*columns, model.last_update).where(
                or_(model.last_update >= previous, model.last_update.is_(None))
            )
            matched = source.execute(statement).all()
            changed[model] = {
                row[0] if len(columns) == 1 else tuple(row[:-1]) for row in matched
            }
            newest = max(
                (row[-1] for row in matched if row[-1] is not None),
                default=previous,
            )
        markers.append({"source_table": name, "last_update": newest, "loaded": True})
    return changed, markers


def affected_rows(source, changed, full):
    if full:
        return changed

    def keys(model, primary_key):
        ids = changed[model]
        return set(source.scalars(select(primary_key))) if ids is None else ids

    result = changed.copy()
    cities = keys(s.City, s.City.city_id) | related_ids(
        source, s.City.city_id, s.City.country_id, keys(s.Country, s.Country.country_id)
    )
    addresses = keys(s.Address, s.Address.address_id) | related_ids(
        source, s.Address.address_id, s.Address.city_id, cities
    )
    for model, key in ((s.Customer, s.Customer.customer_id), (s.Store, s.Store.store_id)):
        result[model] = keys(model, key) | related_ids(source, key, model.address_id, addresses)

    result[s.Film] = keys(s.Film, s.Film.film_id) | related_ids(
        source, s.Film.film_id, s.Film.language_id, keys(s.Language, s.Language.language_id)
    )
    result[s.Rental] = keys(s.Rental, s.Rental.rental_id) | related_ids(
        source, s.Rental.rental_id, s.Rental.inventory_id, keys(s.Inventory, s.Inventory.inventory_id)
    )
    result[s.Payment] = (
        keys(s.Payment, s.Payment.payment_id)
        | related_ids(source, s.Payment.payment_id, s.Payment.rental_id, result[s.Rental])
        | related_ids(
            source, s.Payment.payment_id, s.Payment.staff_id, keys(s.Staff, s.Staff.staff_id),
            s.Payment.rental_id.is_(None),
        )
    )
    return result


def located_rows(model, *columns):
    return (
        select(*columns, s.City.city.label("city"), s.Country.country.label("country"))
        .select_from(model)
        .join(s.Address, model.address_id == s.Address.address_id)
        .join(s.City, s.Address.city_id == s.City.city_id)
        .join(s.Country, s.City.country_id == s.Country.country_id)
    )


def write_dimensions(source, target, ids):
    film = select(
        s.Film.film_id, s.Film.title, s.Film.rating, s.Film.length,
        s.Language.name.label("language"), s.Film.release_year, s.Film.last_update,
    ).join(s.Language, s.Language.language_id == s.Film.language_id)
    customer = located_rows(
        s.Customer, s.Customer.customer_id, s.Customer.first_name,
        s.Customer.last_name, s.Customer.active, s.Customer.last_update,
    )
    store = located_rows(s.Store, s.Store.store_id, s.Store.last_update)

    for src, dst, statement in (
        (s.Film, t.DimFilm, film),
        (s.Actor, t.DimActor, select(s.Actor.actor_id, s.Actor.first_name,
                                    s.Actor.last_name, s.Actor.last_update)),
        (s.Category, t.DimCategory, select(s.Category.category_id, s.Category.name,
                                          s.Category.last_update)),
        (s.Store, t.DimStore, store),
        (s.Customer, t.DimCustomer, customer),
    ):
        key = inspect(src).primary_key[0].key
        data = rows(source, statement, getattr(src, key), ids[src])
        upsert(target, dst, (key,), (dict(row._mapping) for row in data))


def dimension_keys(target):
    return {
        model: dict(target.execute(select(natural_key, surrogate_key)).all())
        for model, natural_key, surrogate_key in (
            (t.DimFilm, t.DimFilm.film_id, t.DimFilm.film_key),
            (t.DimActor, t.DimActor.actor_id, t.DimActor.actor_key),
            (t.DimCategory, t.DimCategory.category_id, t.DimCategory.category_key),
            (t.DimStore, t.DimStore.store_id, t.DimStore.store_key),
            (t.DimCustomer, t.DimCustomer.customer_id, t.DimCustomer.customer_key),
        )
    }


def write_bridges(source, target, ids, keys):
    for src, dst, natural_key, dimension, surrogate_name in (
        (s.FilmActor, t.BridgeFilmActor, s.FilmActor.actor_id, t.DimActor, "actor_key"),
        (s.FilmCategory, t.BridgeFilmCategory, s.FilmCategory.category_id,
         t.DimCategory, "category_key"),
    ):
        data = rows(source, select(src.film_id, natural_key),
                    tuple_(src.film_id, natural_key), ids[src])
        upsert(target, dst, ("film_key", surrogate_name), (
            {"film_key": keys[t.DimFilm][film_id], surrogate_name: keys[dimension][other_id]}
            for film_id, other_id in data
        ))


def write_facts(target, rentals, payments, keys):
    def rental_record(row):
        rented = row.rental_date.date()
        returned = row.return_date.date() if row.return_date else None
        return {
            "rental_id": row.rental_id, "date_key_rented": date_key(rented),
            "date_key_returned": date_key(returned) if returned else None,
            "film_key": keys[t.DimFilm][row.film_id], "store_key": keys[t.DimStore][row.store_id],
            "customer_key": keys[t.DimCustomer][row.customer_id], "staff_id": row.staff_id,
            "rental_duration_days": (returned - rented).days if returned else None,
        }

    upsert(target, t.FactRental, ("rental_id",), map(rental_record, rentals))
    upsert(target, t.FactPayment, ("payment_id",), (
        {
            "payment_id": row.payment_id, "date_key_paid": date_key(row.payment_date.date()),
            "customer_key": keys[t.DimCustomer][row.customer_id],
            "store_key": keys[t.DimStore][row.store_id], "staff_id": row.staff_id,
            "amount": row.amount,
        }
        for row in payments
    ))


def synchronize(source_engine, target_engine, *, full):
    with Session(source_engine) as source, Session(target_engine) as target:
        with source.begin(), target.begin():
            state = {
                row.source_table: row for row in target.scalars(select(t.SyncState)).all()
            }
            if set(state) != {model.__tablename__ for model in s.SOURCE_TABLES}:
                raise ValueError("Run init before syncing")
            if not full and not all(row.loaded for row in state.values()):
                raise ValueError("Run full-load before incremental")

            changed, markers = scan_changes(source, state, full)
            ids = affected_rows(source, changed, full)
            rental_query = select(
                s.Rental.rental_id, s.Rental.rental_date, s.Rental.return_date,
                s.Rental.customer_id, s.Rental.staff_id, s.Inventory.film_id, s.Inventory.store_id,
            ).join(s.Inventory, s.Inventory.inventory_id == s.Rental.inventory_id)
            payment_query = s.with_payment_store(select(
                s.Payment.payment_id, s.Payment.payment_date, s.Payment.customer_id,
                s.Payment.staff_id, s.Payment.amount, s.payment_store().label("store_id"),
            ))
            rentals = rows(source, rental_query, s.Rental.rental_id, ids[s.Rental])
            payments = rows(source, payment_query, s.Payment.payment_id, ids[s.Payment])
            dates = {row.rental_date.date() for row in rentals}
            dates.update(row.return_date.date() for row in rentals if row.return_date)
            dates.update(row.payment_date.date() for row in payments)
            write_dates(target, dates)
            write_dimensions(source, target, ids)
            keys = dimension_keys(target)
            write_bridges(source, target, ids, keys)
            write_facts(target, rentals, payments, keys)
            result = validate(source, target)
            upsert(target, t.SyncState, ("source_table",), markers)

    return f"Processed {len(rentals):,} rental and {len(payments):,} payment rows. {result}"
