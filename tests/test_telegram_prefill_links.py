# -*- coding: utf-8 -*-
"""The failure notification must carry a one-click claim link.

When a run cannot finish (Cloudflare on a datacenter IP, a captcha the model cannot solve), the
cheapest recovery is the user clicking a prefilled checkout URL on a device that is already
signed in. That link is derived from the promotion's namespace/offer id, so it is pinned here.
"""

from models import PromotionGame
from services.telegram_notification_service import prefill_checkout_url


def _game(**overrides):
    payload = {
        "title": "Eternal Threads",
        "id": "0a2d9a5f1b2c4d3e8f7a6b5c4d3e2f1a",
        "namespace": "d5241c76f178492ea1540fce45616757",
        "description": "",
        "offerType": "BASE_GAME",
        "url": "https://store.epicgames.com/en-US/p/eternal-threads",
    }
    payload.update(overrides)
    return PromotionGame(**payload)


def test_prefill_checkout_url_contains_offer_pair():
    url = prefill_checkout_url(_game())

    assert url.startswith("https://www.epicgames.com/store/purchase?")
    assert "offers=1-d5241c76f178492ea1540fce45616757-0a2d9a5f1b2c4d3e8f7a6b5c4d3e2f1a" in url


def test_prefill_checkout_url_is_empty_without_identifiers():
    assert prefill_checkout_url(_game(namespace="")) == ""
    assert prefill_checkout_url(_game(id="")) == ""
