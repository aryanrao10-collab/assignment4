"""Mapped columns needed from the existing Sakila database."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, FetchedValue, Numeric, String, case, func
from sqlalchemy.dialects.mysql import YEAR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class SourceBase(DeclarativeBase):
    pass


class UpdatedRow:
    last_update: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp(), server_onupdate=FetchedValue()
    )


class NullableUpdatedRow:
    last_update: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=func.current_timestamp(), server_onupdate=FetchedValue()
    )


class Film(UpdatedRow, SourceBase):
    __tablename__ = "film"

    film_id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(128))
    rating: Mapped[str | None] = mapped_column(String(5))
    length: Mapped[int | None]
    language_id: Mapped[int]
    release_year: Mapped[int | None] = mapped_column(YEAR)


class Language(UpdatedRow, SourceBase):
    __tablename__ = "language"

    language_id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(20))


class Actor(UpdatedRow, SourceBase):
    __tablename__ = "actor"

    actor_id: Mapped[int] = mapped_column(primary_key=True)
    first_name: Mapped[str] = mapped_column(String(45))
    last_name: Mapped[str] = mapped_column(String(45))


class Category(UpdatedRow, SourceBase):
    __tablename__ = "category"

    category_id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(25))


class FilmActor(UpdatedRow, SourceBase):
    __tablename__ = "film_actor"

    film_id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int] = mapped_column(primary_key=True)


class FilmCategory(UpdatedRow, SourceBase):
    __tablename__ = "film_category"

    film_id: Mapped[int] = mapped_column(primary_key=True)
    category_id: Mapped[int] = mapped_column(primary_key=True)


class Inventory(UpdatedRow, SourceBase):
    __tablename__ = "inventory"

    inventory_id: Mapped[int] = mapped_column(primary_key=True)
    film_id: Mapped[int]
    store_id: Mapped[int]


class Rental(UpdatedRow, SourceBase):
    __tablename__ = "rental"

    rental_id: Mapped[int] = mapped_column(primary_key=True)
    rental_date: Mapped[datetime]
    inventory_id: Mapped[int]
    customer_id: Mapped[int]
    return_date: Mapped[datetime | None]
    staff_id: Mapped[int]


class Payment(NullableUpdatedRow, SourceBase):
    __tablename__ = "payment"

    payment_id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int]
    staff_id: Mapped[int]
    rental_id: Mapped[int | None]
    amount: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    payment_date: Mapped[datetime]


class Store(UpdatedRow, SourceBase):
    __tablename__ = "store"

    store_id: Mapped[int] = mapped_column(primary_key=True)
    address_id: Mapped[int]


class Staff(UpdatedRow, SourceBase):
    __tablename__ = "staff"

    staff_id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int]


class Customer(NullableUpdatedRow, SourceBase):
    __tablename__ = "customer"

    customer_id: Mapped[int] = mapped_column(primary_key=True)
    first_name: Mapped[str] = mapped_column(String(45))
    last_name: Mapped[str] = mapped_column(String(45))
    active: Mapped[bool] = mapped_column(Boolean)
    address_id: Mapped[int]


class Address(UpdatedRow, SourceBase):
    __tablename__ = "address"

    address_id: Mapped[int] = mapped_column(primary_key=True)
    city_id: Mapped[int]


class City(UpdatedRow, SourceBase):
    __tablename__ = "city"

    city_id: Mapped[int] = mapped_column(primary_key=True)
    city: Mapped[str] = mapped_column(String(50))
    country_id: Mapped[int]


class Country(UpdatedRow, SourceBase):
    __tablename__ = "country"

    country_id: Mapped[int] = mapped_column(primary_key=True)
    country: Mapped[str] = mapped_column(String(50))


SOURCE_TABLES = (
    Film, Language, Actor, Category, FilmActor, FilmCategory, Inventory, Rental,
    Payment, Store, Staff, Customer, Address, City, Country,
)


def payment_store():
    return case((Payment.rental_id.is_(None), Staff.store_id), else_=Inventory.store_id)


def with_payment_store(statement):
    return (
        statement.select_from(Payment)
        .join(Staff, Staff.staff_id == Payment.staff_id)
        .outerjoin(Rental, Rental.rental_id == Payment.rental_id)
        .outerjoin(Inventory, Inventory.inventory_id == Rental.inventory_id)
    )
