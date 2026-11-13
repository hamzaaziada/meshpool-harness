from cart import cart_total


def test_simple():
    assert cart_total([{"price": 10.0, "qty": 2}]) == 24.0
