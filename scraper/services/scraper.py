"""Unified product scraper that routes to Amazon or Walmart."""

from __future__ import annotations

import re

from .amazon_scraper import ProductData, scrape_amazon_product
from .walmart_scraper import item_id_to_url, scrape_walmart_product


def detect_store(url: str) -> str:
    lowered = url.lower()
    if "walmart.com" in lowered or "walmart.ca" in lowered:
        return "walmart"
    if "amazon." in lowered:
        return "amazon"
    raise ValueError("Unsupported store. Please use an Amazon or Walmart product URL.")


def scrape_product(url: str) -> ProductData:
    """Scrape a product page from Amazon or Walmart."""
    store = detect_store(url)
    if store == "walmart":
        return scrape_walmart_product(url)
    return scrape_amazon_product(url)


def scrape_variant(store: str, product_id: str, base_url: str | None = None) -> ProductData:
    """Scrape a specific product variant by store-specific ID."""
    if store == "walmart":
        if not re.fullmatch(r"\d+", product_id):
            raise ValueError("Invalid Walmart item ID.")
        return scrape_walmart_product(item_id_to_url(product_id, base_url))

    if not re.fullmatch(r"[A-Z0-9]{10}", product_id):
        raise ValueError("Invalid ASIN.")

    from .amazon_scraper import asin_to_url

    return scrape_amazon_product(asin_to_url(product_id, base_url))
