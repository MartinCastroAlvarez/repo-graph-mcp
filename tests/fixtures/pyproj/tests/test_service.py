from shop.service import make_checkout


def test_make_checkout() -> None:
    assert make_checkout() is not None


def test_checkout_flow() -> None:
    make_checkout().run([1])
