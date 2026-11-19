from shop.services.pricing import final_price


def test_price():
    assert final_price(100, 0.1) == 110.0
