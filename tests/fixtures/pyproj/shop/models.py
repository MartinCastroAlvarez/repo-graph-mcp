"""Domain models."""

import json


class Base:
    """Persistence base class."""

    def save(self) -> None:
        print(json.dumps({"saved": type(self).__name__}))


class Order(Base):
    """An order with line items."""

    items: list[int]

    def __init__(self, items: list[int]) -> None:
        self.items = items

    def total(self) -> float:
        """Sum of the items plus tax."""
        return sum(self.items) + self._tax()

    def _tax(self) -> float:
        return 0.0

    def save(self) -> None:
        super().save()
        audit("saved")


def audit(message: str) -> None:
    """Record an audit message."""
    print(message)


DEFAULT_CURRENCY = "EUR"
