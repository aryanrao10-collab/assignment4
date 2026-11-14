"""SQLite analytics models and indexes."""

from datetime import date as calendar_date
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Numeric, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class TargetBase(DeclarativeBase):
    pass


class DimDate(TargetBase):
    __tablename__ = "dim_date"

    date_key: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    date: Mapped[calendar_date] = mapped_column(Date)
    year: Mapped[int]
    quarter: Mapped[int]
    month: Mapped[int]
    day_of_month: Mapped[int]
    day_of_week: Mapped[int]
    is_weekend: Mapped[int]


class DimFilm(TargetBase):
    __tablename__ = "dim_film"
    __table_args__ = {"sqlite_autoincrement": True}

    film_key: Mapped[int] = mapped_column(primary_key=True)
    film_id: Mapped[int] = mapped_column(unique=True, index=True)
    title: Mapped[str] = mapped_column(String(128))
    rating: Mapped[str | None] = mapped_column(String(5))
    length: Mapped[int | None]
    language: Mapped[str] = mapped_column(String(20))
    release_year: Mapped[int | None]
    last_update: Mapped[datetime]


class DimActor(TargetBase):
    __tablename__ = "dim_actor"
    __table_args__ = {"sqlite_autoincrement": True}

    actor_key: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int] = mapped_column(unique=True, index=True)
    first_name: Mapped[str] = mapped_column(String(45))
    last_name: Mapped[str] = mapped_column(String(45))
    last_update: Mapped[datetime]


class DimCategory(TargetBase):
    __tablename__ = "dim_category"
    __table_args__ = {"sqlite_autoincrement": True}

    category_key: Mapped[int] = mapped_column(primary_key=True)
    category_id: Mapped[int] = mapped_column(unique=True, index=True)
    name: Mapped[str] = mapped_column(String(25))
    last_update: Mapped[datetime]


class DimStore(TargetBase):
    __tablename__ = "dim_store"
    __table_args__ = {"sqlite_autoincrement": True}

    store_key: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(unique=True, index=True)
    city: Mapped[str] = mapped_column(String(50))
    country: Mapped[str] = mapped_column(String(50))
    last_update: Mapped[datetime]


class DimCustomer(TargetBase):
    __tablename__ = "dim_customer"
    __table_args__ = {"sqlite_autoincrement": True}

    customer_key: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(unique=True, index=True)
    first_name: Mapped[str] = mapped_column(String(45))
    last_name: Mapped[str] = mapped_column(String(45))
    active: Mapped[bool]
    city: Mapped[str] = mapped_column(String(50))
    country: Mapped[str] = mapped_column(String(50))
    last_update: Mapped[datetime | None]


class BridgeFilmActor(TargetBase):
    __tablename__ = "bridge_film_actor"

    film_key: Mapped[int] = mapped_column(ForeignKey("dim_film.film_key"), primary_key=True)
    actor_key: Mapped[int] = mapped_column(ForeignKey("dim_actor.actor_key"), primary_key=True)


class BridgeFilmCategory(TargetBase):
    __tablename__ = "bridge_film_category"

    film_key: Mapped[int] = mapped_column(ForeignKey("dim_film.film_key"), primary_key=True)
    category_key: Mapped[int] = mapped_column(
        ForeignKey("dim_category.category_key"), primary_key=True
    )


class FactRental(TargetBase):
    __tablename__ = "fact_rental"
    __table_args__ = {"sqlite_autoincrement": True}

    fact_rental_key: Mapped[int] = mapped_column(primary_key=True)
    rental_id: Mapped[int] = mapped_column(unique=True, index=True)
    date_key_rented: Mapped[int] = mapped_column(ForeignKey("dim_date.date_key"), index=True)
    date_key_returned: Mapped[int | None] = mapped_column(
        ForeignKey("dim_date.date_key"), index=True
    )
    film_key: Mapped[int] = mapped_column(ForeignKey("dim_film.film_key"), index=True)
    store_key: Mapped[int] = mapped_column(ForeignKey("dim_store.store_key"), index=True)
    customer_key: Mapped[int] = mapped_column(ForeignKey("dim_customer.customer_key"), index=True)
    staff_id: Mapped[int]
    rental_duration_days: Mapped[int | None]


class FactPayment(TargetBase):
    __tablename__ = "fact_payment"
    __table_args__ = {"sqlite_autoincrement": True}

    fact_payment_key: Mapped[int] = mapped_column(primary_key=True)
    payment_id: Mapped[int] = mapped_column(unique=True, index=True)
    date_key_paid: Mapped[int] = mapped_column(ForeignKey("dim_date.date_key"), index=True)
    customer_key: Mapped[int] = mapped_column(ForeignKey("dim_customer.customer_key"), index=True)
    store_key: Mapped[int] = mapped_column(ForeignKey("dim_store.store_key"), index=True)
    staff_id: Mapped[int]
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))


class SyncState(TargetBase):
    __tablename__ = "sync_state"

    source_table: Mapped[str] = mapped_column(String(32), primary_key=True)
    last_update: Mapped[datetime | None]
    loaded: Mapped[bool]
