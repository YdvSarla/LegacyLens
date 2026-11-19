"""HTTP views for the shop."""
from flask import Blueprint, jsonify

from .services.pricing import final_price
from shop.models import Order

bp = Blueprint("shop", __name__, url_prefix="/shop")


@bp.route("/price/<int:amount>", methods=["GET", "POST"])
def price(amount):
    """Return the final price."""
    return jsonify(final_price(amount, 0.1))


@bp.get("/orders")
def orders():
    return jsonify(Order.query.all())
