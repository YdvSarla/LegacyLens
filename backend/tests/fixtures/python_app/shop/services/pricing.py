"""Pricing rules."""
import subprocess


def final_price(amount, tax):
    """Apply tax to an amount."""
    if amount < 0:
        raise ValueError("negative")
    return round(amount * (1 + tax), 2)


def discount(amount, code=None):
    if code == "HALF" and amount > 0:
        return amount / 2
    return amount


class Cart:
    def __init__(self):
        self.items = []

    def add(self, price):
        self.items.append(price)
        return self.total()

    def total(self):
        return sum(self.items)


def run_report(name):
    return subprocess.run(f"report {name}", shell=True)
