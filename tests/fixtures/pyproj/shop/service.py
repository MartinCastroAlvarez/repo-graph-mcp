"""Checkout service."""

from __future__ import annotations

from shop.models import Order
from shop.models import audit

from . import models


class Repo:
    def store(self, order: Order) -> None:
        audit(f"stored {order}")


class Checkout:
    """Runs a checkout."""

    def __init__(self, repo: Repo) -> None:
        self.repo = repo

    def run(self, items: list[int]) -> Order:
        order = Order(items)
        order.save()
        models.audit("run")
        self.repo.store(order)
        return order


def make_checkout() -> Checkout:
    return Checkout(Repo())


def main() -> float:
    return make_checkout().run([1, 2]).total()
