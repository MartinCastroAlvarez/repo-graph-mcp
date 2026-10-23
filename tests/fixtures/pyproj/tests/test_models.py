from shop.models import Order


def test_total() -> None:
    assert Order([1, 2]).total() == 3


class TestOrder:
    def test_save(self) -> None:
        Order([]).save()
