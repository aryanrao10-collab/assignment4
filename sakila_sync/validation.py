"""Validate analytics coverage and store totals using both sessions."""

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select

from . import source as s
from . import target as t

DIMENSIONS = (
    (s.Film.film_id, t.DimFilm.film_id),
    (s.Actor.actor_id, t.DimActor.actor_id),
    (s.Category.category_id, t.DimCategory.category_id),
    (s.Store.store_id, t.DimStore.store_id),
    (s.Customer.customer_id, t.DimCustomer.customer_id),
)


class ValidationError(ValueError):
    pass


def verify_keys(source, target, label, source_query, target_query, errors):
    expected = source.execute(source_query).all()
    observed = target.execute(target_query).all()
    if len(expected) != len(observed):
        errors.append(f"{label} count mismatch: MySQL {len(expected):,}, SQLite {len(observed):,}")
    missing = set(expected) - set(observed)
    extra = set(observed) - set(expected)
    if missing or extra:
        errors.append(f"{label} key mismatch: {len(missing):,} missing, {len(extra):,} extra")
    return len(expected)


def integrity(target, errors):
    natural_keys = {key.class_.__table__: (key,) for _, key in DIMENSIONS}
    natural_keys[t.FactRental.__table__] = (t.FactRental.rental_id,)
    natural_keys[t.FactPayment.__table__] = (t.FactPayment.payment_id,)
    for table in t.TargetBase.metadata.sorted_tables:
        keys = natural_keys.get(table, tuple(table.primary_key.columns))
        duplicate = target.execute(
            select(*keys).select_from(table).group_by(*keys).having(func.count() > 1).limit(1)
        ).first()
        if duplicate is not None:
            errors.append(f"{table.name} has duplicate natural keys")

        for fk in sorted(table.foreign_keys, key=lambda item: item.parent.name):
            orphan = target.scalar(
                select(func.count()).select_from(
                    table.outerjoin(fk.column.table, fk.parent == fk.column)
                ).where(fk.parent.is_not(None), fk.column.is_(None))
            )
            if orphan:
                errors.append(f"{table.name}.{fk.parent.name} has {orphan:,} orphan rows")


def date_conditions(column, start, end, *, integer_keys=False):
    conditions = []
    if start:
        conditions.append(column >= (int(start.strftime("%Y%m%d")) if integer_keys else start))
    if end:
        if integer_keys:
            conditions.append(column <= int(end.strftime("%Y%m%d")))
        elif end != date.max:
            conditions.append(column < end + timedelta(days=1))
    return conditions


def default_period(source):
    latest = source.scalar(select(func.max(s.Payment.payment_date)))
    if latest is None:
        return None, None
    end = latest.date()
    return end - timedelta(days=29), end


def compare_by_store(source, target, start, end, errors):
    source_rental = select(
        s.Inventory.store_id, func.count(s.Rental.rental_id),
    ).select_from(s.Rental).join(
        s.Inventory, s.Rental.inventory_id == s.Inventory.inventory_id,
    ).where(*date_conditions(s.Rental.rental_date, start, end)).group_by(s.Inventory.store_id)
    target_rental = select(
        t.DimStore.store_id, func.count(t.FactRental.rental_id),
    ).select_from(t.FactRental).join(
        t.DimStore, t.FactRental.store_key == t.DimStore.store_key,
    ).where(*date_conditions(
        t.FactRental.date_key_rented, start, end, integer_keys=True,
    )).group_by(t.DimStore.store_id)

    expected_rentals = dict(source.execute(source_rental).all())
    actual_rentals = dict(target.execute(target_rental).all())
    for store in sorted(expected_rentals.keys() | actual_rentals.keys()):
        expected = expected_rentals.get(store, 0)
        observed = actual_rentals.get(store, 0)
        if expected != observed:
            errors.append(f"store {store} rental count mismatch: MySQL {expected}, SQLite {observed}")

    store_id = s.payment_store()
    source_payment = s.with_payment_store(select(
        store_id, func.count(s.Payment.payment_id), func.sum(s.Payment.amount),
    )).where(*date_conditions(s.Payment.payment_date, start, end)).group_by(store_id)
    target_payment = select(
        t.DimStore.store_id, func.count(t.FactPayment.payment_id), func.sum(t.FactPayment.amount),
    ).select_from(t.FactPayment).join(
        t.DimStore, t.FactPayment.store_key == t.DimStore.store_key,
    ).where(*date_conditions(
        t.FactPayment.date_key_paid, start, end, integer_keys=True,
    )).group_by(t.DimStore.store_id)

    expected_payments = {store: (count, amount) for store, count, amount in source.execute(source_payment)}
    actual_payments = {store: (count, amount) for store, count, amount in target.execute(target_payment)}
    zero = (0, Decimal("0.00"))
    for store in sorted(expected_payments.keys() | actual_payments.keys()):
        expected_count, expected_amount = expected_payments.get(store, zero)
        actual_count, actual_amount = actual_payments.get(store, zero)
        if expected_count != actual_count:
            errors.append(
                f"store {store} payment count mismatch: MySQL {expected_count}, SQLite {actual_count}"
            )
        if abs(expected_amount - actual_amount) > Decimal("0.01"):
            errors.append(
                f"store {store} payment total mismatch: "
                f"MySQL ${expected_amount:,.2f}, SQLite ${actual_amount:,.2f}"
            )
    return sum((amount for _, amount in actual_payments.values()), zero[1])


def validate(source, target, start=None, end=None, *, use_default_period=False):
    if use_default_period and start is None and end is None:
        start, end = default_period(source)
    if start and end and start > end:
        raise ValidationError("The start date must be on or before the end date")
    errors = []
    integrity(target, errors)
    for src, dst in DIMENSIONS:
        verify_keys(source, target, dst.class_.__tablename__, select(src), select(dst), errors)

    for model, bridge, dim, source_key, target_key, dimension_key in (
        (s.FilmActor, t.BridgeFilmActor, t.DimActor, s.FilmActor.actor_id,
         t.BridgeFilmActor.actor_key, t.DimActor.actor_key),
        (s.FilmCategory, t.BridgeFilmCategory, t.DimCategory, s.FilmCategory.category_id,
         t.BridgeFilmCategory.category_key, t.DimCategory.category_key),
    ):
        query = select(t.DimFilm.film_id, getattr(dim, source_key.key)).select_from(bridge).join(
            t.DimFilm, bridge.film_key == t.DimFilm.film_key,
        ).join(dim, target_key == dimension_key)
        verify_keys(source, target, bridge.__tablename__, select(model.film_id, source_key), query, errors)

    expected_dates = set()
    for timestamp in (s.Rental.rental_date, s.Rental.return_date, s.Payment.payment_date):
        expected_dates.update(
            value if isinstance(value, date) else date.fromisoformat(value)
            for value in source.scalars(select(func.date(timestamp)).distinct()) if value is not None
        )
    missing_dates = expected_dates - set(target.scalars(select(t.DimDate.date)))
    if missing_dates:
        errors.append(f"dim_date is missing {len(missing_dates):,} source dates")

    rentals, payments = [
        verify_keys(
            source, target, label,
            select(source_id).where(*date_conditions(source_date, start, end)),
            select(target_id).where(*date_conditions(target_date, start, end, integer_keys=True)),
            errors,
        )
        for label, source_id, source_date, target_id, target_date in (
            ("rental", s.Rental.rental_id, s.Rental.rental_date,
             t.FactRental.rental_id, t.FactRental.date_key_rented),
            ("payment", s.Payment.payment_id, s.Payment.payment_date,
             t.FactPayment.payment_id, t.FactPayment.date_key_paid),
        )
    ]
    amount = compare_by_store(source, target, start, end, errors)
    if errors:
        raise ValidationError("; ".join(errors))

    if start and end:
        period = f"{start} through {end}"
    elif start:
        period = f"from {start}"
    elif end:
        period = f"through {end}"
    else:
        period = "all dates"
    return f"Validated {period}: {rentals:,} rentals, {payments:,} payments, ${amount:,.2f}."
