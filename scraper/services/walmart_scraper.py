"""
Walmart product page scraper.

Uses curl_cffi with Safari impersonation to bypass Walmart's bot checks and
parses the embedded __NEXT_DATA__ payload (includes full specifications from
the "View full specifications" modal).
"""

from __future__ import annotations

import json
import re

from bs4 import BeautifulSoup
from curl_cffi import requests

from .amazon_scraper import (
    ProductData,
    add_specification,
    clean_text,
    extract_currency,
    merge_specification_sections,
    normalize_section_name,
)

HEADERS = {
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

IMPERSONATE = "safari17_2_ios"


def extract_item_id(url: str) -> str | None:
    """Extract the numeric Walmart item ID from a product URL."""
    match = re.search(r"/ip(?:/[^/]+)?/(\d+)", url, re.IGNORECASE)
    return match.group(1) if match else None


def item_id_to_url(item_id: str, base_url: str | None = None) -> str:
    """Build a Walmart product URL for a given item ID."""
    domain = "https://www.walmart.com"
    if base_url:
        match = re.search(r"(https?://[^/]+)", base_url)
        if match:
            domain = match.group(1)
    return f"{domain}/ip/{item_id}"


def fetch_page(url: str, timeout: int = 60) -> str:
    """Download a Walmart product page."""
    response = requests.get(
        url,
        headers=HEADERS,
        impersonate=IMPERSONATE,
        timeout=timeout,
    )
    response.raise_for_status()

    if "Robot or human" in response.text:
        raise RuntimeError(
            "Walmart returned a bot-check page. Try again later or use a different IP."
        )

    if "__NEXT_DATA__" not in response.text:
        raise RuntimeError("Unexpected page content. Product data was not found.")

    return response.text


def parse_next_data(html: str) -> dict:
    match = re.search(
        r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
        html,
        re.DOTALL,
    )
    if not match:
        raise RuntimeError("Walmart product data was not found on this page.")

    payload = json.loads(match.group(1))
    return payload["props"]["pageProps"]["initialData"]["data"]


def html_to_text(html: str | None) -> str | None:
    if not html:
        return None
    soup = BeautifulSoup(html, "lxml")
    return clean_text(soup.get_text(" ", strip=True))


def parse_bullet_list(html: str | None) -> list[str]:
    if not html:
        return []

    soup = BeautifulSoup(html, "lxml")
    bullets: list[str] = []
    seen: set[str] = set()

    for item in soup.select("li"):
        text = clean_text(item.get_text(" ", strip=True))
        if text and text not in seen:
            seen.add(text)
            bullets.append(text)

    if bullets:
        return bullets

    text = html_to_text(html)
    return [text] if text else []


def parse_about_this_item(idml: dict) -> list[str]:
    gen_ai = idml.get("genAiDetails") or {}
    bullets = parse_bullet_list(gen_ai.get("genAiDescriptionBullet"))
    if bullets:
        return bullets

    highlights = idml.get("productHighlights") or []
    if highlights:
        formatted: list[str] = []
        for item in highlights:
            name = clean_text(item.get("name"))
            value = clean_text(item.get("value"))
            if name and value:
                formatted.append(f"{name}: {value}")
            elif value:
                formatted.append(value)
        if formatted:
            return formatted

    return parse_bullet_list(idml.get("longDescription"))


def parse_specification_sections(idml: dict) -> dict[str, dict[str, str]]:
    sections: dict[str, dict[str, str]] = {}

    for group in idml.get("specificationsV2") or []:
        section_name = normalize_section_name(group.get("groupName") or "Specifications")
        section_specs: dict[str, str] = {}

        for item in group.get("specificationGroup") or []:
            name = clean_text(item.get("displayName"))
            values = item.get("attributeValue") or []
            if isinstance(values, list):
                value = clean_text(", ".join(str(value) for value in values if value))
            else:
                value = clean_text(str(values))
            add_specification(section_specs, name, value)

        if section_specs:
            existing = sections.setdefault(section_name, {})
            existing.update(section_specs)

    flat_specs: dict[str, str] = {}
    for item in idml.get("specifications") or []:
        add_specification(flat_specs, item.get("name"), item.get("value"))

    if flat_specs and not sections:
        sections["Specifications"] = flat_specs

    return sections


def parse_specifications(idml: dict) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    sections = parse_specification_sections(idml)
    specs = merge_specification_sections(sections)

    warranty = idml.get("warranty")
    if isinstance(warranty, dict):
        warranty_text = clean_text(warranty.get("information"))
        if warranty_text:
            add_specification(specs, "Warranty", warranty_text)
            sections.setdefault("Warranty", {})["Warranty"] = warranty_text
    elif isinstance(warranty, str):
        warranty_text = clean_text(warranty)
        if warranty_text:
            add_specification(specs, "Warranty", warranty_text)
            sections.setdefault("Warranty", {})["Warranty"] = warranty_text

    return specs, sections


def parse_price(product: dict) -> tuple[str | None, str | None]:
    price_info = product.get("priceInfo") or {}
    current = price_info.get("currentPrice") or {}
    price_string = clean_text(current.get("priceString") or current.get("variantPriceString"))
    if not price_string and current.get("price") is not None:
        price_string = f"${current['price']:.2f}"

    currency = clean_text(current.get("currencyUnit"))
    return price_string, currency or extract_currency(price_string or "")


def parse_rating(product: dict) -> tuple[str | None, str | None]:
    rating = product.get("averageRating")
    review_count = product.get("numberOfReviews")

    rating_text = None
    if rating is not None:
        rating_text = f"{rating} out of 5 stars"

    review_text = None
    if review_count is not None:
        review_text = f"{review_count:,}" if isinstance(review_count, int) else str(review_count)

    return rating_text, review_text


def parse_availability(product: dict) -> str | None:
    status = clean_text(product.get("availabilityStatus"))
    if status:
        return status.replace("_", " ").title()

    offer = product.get("primaryOffer") or {}
    offer_status = clean_text(offer.get("availabilityStatus"))
    if offer_status:
        return offer_status.replace("_", " ").title()

    return None


def parse_images(product: dict) -> list[str]:
    images: list[str] = []
    seen: set[str] = set()

    image_info = product.get("imageInfo") or {}
    for item in image_info.get("allImages") or []:
        url = item.get("url")
        if url and url not in seen:
            seen.add(url)
            images.append(url)

    return images


def get_selected_variant_ids(product: dict) -> list[str]:
    selected: list[str] = []

    for criterion in product.get("variantCriteria") or []:
        for option in criterion.get("variantList") or []:
            if option.get("selected"):
                option_id = option.get("id")
                if option_id:
                    selected.append(option_id)

    if selected:
        return selected

    return list(product.get("selectedVariantIds") or [])


def find_us_item_id(product: dict, selection_ids: list[str]) -> str | None:
    variants_map = product.get("variantsMap") or {}
    if not variants_map:
        return None

    desired = set(selection_ids)
    if not desired:
        return None

    for entry in variants_map.values():
        variant_ids = set(entry.get("variants") or [])
        if variant_ids == desired:
            item_id = entry.get("usItemId")
            return str(item_id) if item_id else None

    best_match: str | None = None
    best_score = -1
    for entry in variants_map.values():
        variant_ids = set(entry.get("variants") or [])
        if not desired.issubset(variant_ids):
            continue
        score = len(variant_ids)
        if best_match is None or score < best_score:
            best_score = score
            item_id = entry.get("usItemId")
            best_match = str(item_id) if item_id else None

    return best_match


def build_selection_for_option(
    product: dict,
    criterion_id: str,
    option_id: str,
) -> list[str]:
    selection: list[str] = []

    for criterion in product.get("variantCriteria") or []:
        current_id = criterion.get("id")
        if not current_id:
            continue

        if current_id == criterion_id:
            selection.append(option_id)
            continue

        for option in criterion.get("variantList") or []:
            if option.get("selected"):
                selected_id = option.get("id")
                if selected_id:
                    selection.append(selected_id)
                break

    return selection


def parse_variant_option(
    product: dict,
    criterion: dict,
    option: dict,
) -> dict | None:
    option_id = option.get("id")
    label = clean_text(option.get("name"))
    if not option_id or not label:
        return None

    criterion_id = criterion.get("id") or ""
    selection = build_selection_for_option(product, criterion_id, option_id)
    item_id = find_us_item_id(product, selection)

    variants_map = product.get("variantsMap") or {}
    entry = None
    products = option.get("products") or []
    if len(products) == 1 and products[0] in variants_map:
        entry = variants_map[products[0]]
    elif item_id:
        for candidate in variants_map.values():
            if str(candidate.get("usItemId")) == item_id:
                entry = candidate
                break

    price = None
    currency = None
    availability = clean_text(option.get("availabilityStatus"))
    image = option.get("swatchImageUrl")

    if entry:
        item_id = str(entry.get("usItemId") or item_id or "")
        price_info = entry.get("priceInfo") or {}
        current = price_info.get("currentPrice") or {}
        price = clean_text(current.get("priceString") or current.get("variantPriceString"))
        currency = clean_text(current.get("currencyUnit"))
        if not availability:
            availability = clean_text(entry.get("availabilityStatus"))
        if not image:
            image_info = entry.get("imageInfo") or {}
            all_images = image_info.get("allImages") or []
            if all_images:
                image = all_images[0].get("url")

    unavailable = False
    if availability:
        unavailable = availability.upper() in {"OUT_OF_STOCK", "UNAVAILABLE"}

    return {
        "asin": item_id or "",
        "label": label,
        "image": image,
        "price": price,
        "currency": currency,
        "availability": availability.replace("_", " ").title() if availability else None,
        "selected": bool(option.get("selected")),
        "unavailable": unavailable,
        "hidden": False,
    }


def parse_variations(product: dict) -> list[dict]:
    variations: list[dict] = []

    for criterion in product.get("variantCriteria") or []:
        options: list[dict] = []
        for option in criterion.get("variantList") or []:
            parsed = parse_variant_option(product, criterion, option)
            if parsed and parsed.get("asin"):
                options.append(parsed)

        if not options:
            continue

        criterion_type = (criterion.get("type") or "").upper()
        if criterion_type == "DROPDOWN":
            display_type = "dropdown"
        elif any(option.get("image") for option in options):
            display_type = "image"
        else:
            display_type = "text"

        variations.append(
            {
                "id": criterion.get("id") or criterion.get("name", "variant"),
                "label": clean_text(criterion.get("name")) or "Option",
                "type": display_type,
                "has_hidden_options": len(options) > 12,
                "options": options,
            }
        )

    return variations


def parse_product_data(data: dict, url: str) -> ProductData:
    product = data.get("product") or {}
    idml = data.get("idml") or {}

    price, currency = parse_price(product)
    rating, review_count = parse_rating(product)
    specifications, specification_sections = parse_specifications(idml)
    about_this_item = parse_about_this_item(idml)

    description = html_to_text(idml.get("longDescription"))
    if not description:
        description = clean_text(idml.get("shortDescription") or product.get("shortDescription"))

    item_id = str(product.get("usItemId") or extract_item_id(url) or "")
    canonical_url = product.get("canonicalUrl")
    if canonical_url:
        if canonical_url.startswith("http"):
            resolved_url = canonical_url
        else:
            resolved_url = f"https://www.walmart.com{canonical_url}"
    else:
        resolved_url = item_id_to_url(item_id) if item_id else url.split("?")[0]

    return ProductData(
        url=resolved_url,
        store="walmart",
        asin=item_id or None,
        title=clean_text(product.get("name")),
        price=price,
        currency=currency,
        rating=rating,
        review_count=review_count,
        availability=parse_availability(product),
        brand=clean_text(product.get("brand")),
        about_this_item=about_this_item,
        feature_bullets=about_this_item,
        description=description,
        specifications=specifications,
        specification_sections=specification_sections,
        images=parse_images(product),
        variations=parse_variations(product),
    )


def scrape_walmart_product(url: str) -> ProductData:
    """Scrape a single Walmart product page."""
    html = fetch_page(url)
    data = parse_next_data(html)
    return parse_product_data(data, url.split("?")[0])
