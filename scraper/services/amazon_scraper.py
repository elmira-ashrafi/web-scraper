"""
Amazon product page scraper.

Uses curl_cffi to mimic a real browser and bypass Amazon's basic bot detection.
"""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import asdict, dataclass, field

from bs4 import BeautifulSoup
from curl_cffi import requests


DEFAULT_URL = (
    "https://www.amazon.com/SAMSUNG-Android-Durability-Included-Moonstone/dp/B0D851Z6NQ/ref=sr_1_1?_encoding=UTF8&content-id=amzn1.sym.f0670b1b-e1fd-4c67-a2b1-b8a347243628&dib=eyJ2IjoiMSJ9.W_F8CDdZJoOxq8vcbVshUF7bSuGWZWnF-VLPyPWyzFg4Eh8xv8LUUDIW2vk3nJc3ma8xgXm2ifrKlyWG_7TQV_-hS6XMNkCR3DQjLACMQIYaHsr4f5RaHUG1eHXdKr6JCe9VMIqLp65HmrYKK3tjVSOAwMF3GQs6cFuRe9I6lnzWc1XmBVcn59YmYKdopBqPpGzMziCpzP3gqCTvgtpYbHT49Wc5siJ_U2tv-FmDsjE.KvXFfu_wldORnm1G7bP8XMYU62t30wc5yFs1mjA6X7o&dib_tag=se&keywords=electronic+tablets&pd_rd_r=d2faab39-d7bf-4496-b520-53c5f3442622&pd_rd_w=reBbU&pd_rd_wg=kMzVi&qid=1786445039&sr=8-1"
)

HEADERS = {
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


@dataclass
class ProductData:
    url: str
    store: str = "amazon"
    asin: str | None = None
    title: str | None = None
    price: str | None = None
    currency: str | None = None
    rating: str | None = None
    review_count: str | None = None
    availability: str | None = None
    brand: str | None = None
    about_this_item: list[str] = field(default_factory=list)
    feature_bullets: list[str] = field(default_factory=list)
    description: str | None = None
    about_the_author: str | None = None
    author_image: str | None = None
    about_sections: dict[str, str] = field(default_factory=dict)
    specifications: dict[str, str] = field(default_factory=dict)
    specification_sections: dict[str, dict[str, str]] = field(default_factory=dict)
    images: list[str] = field(default_factory=list)
    variations: list[dict] = field(default_factory=list)


SKIP_SPEC_KEYS = {
    "customer reviews",
    "best sellers rank",
    "feedback",
}


def extract_asin(url: str) -> str | None:
    """Extract ASIN from an Amazon product URL."""
    match = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})", url, re.IGNORECASE)
    return match.group(1).upper() if match else None


def clean_text(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = re.sub(r"\s+", " ", value).strip()
    return cleaned or None


def fetch_page(url: str, timeout: int = 30) -> str:
    """Download an Amazon product page."""
    response = requests.get(
        url,
        headers=HEADERS,
        impersonate="chrome120",
        timeout=timeout,
    )
    response.raise_for_status()

    if "validateCaptcha" in response.text:
        raise RuntimeError(
            "Amazon returned a CAPTCHA page. Try again later or use a different IP."
        )

    if "productTitle" not in response.text:
        raise RuntimeError("Unexpected page content. Product data was not found.")

    return response.text


def extract_currency(price_text: str, soup: BeautifulSoup | None = None) -> str | None:
    """Extract currency code or symbol from a price string or page markup."""
    if price_text:
        code_match = re.match(r"^([A-Z]{3})(?=\d|\s|,|\.)", price_text.strip())
        if code_match:
            return code_match.group(1)

        symbol_match = re.search(r"([$€£¥₺])", price_text)
        if symbol_match:
            return symbol_match.group(1)

    if soup:
        symbol_element = soup.select_one(
            ".a-price .a-price-symbol, "
            "#corePriceDisplay_desktop_feature_div .a-price-symbol, "
            "#corePrice_feature_div .a-price-symbol"
        )
        if symbol_element:
            return clean_text(symbol_element.get_text())

    return None


PRICE_EXCLUDE_PATTERNS = (
    "carousel",
    "sims-consolidated",
    "sp_detail",
    "sp_offervertical",
    "p13n-asin",
    "p13n-sc",
    "similarities",
    "sponsored",
    "multi-brand-video",
    "recs-carousel",
    "purchase-sims",
    "hz-lists",
    "customer-reviews",
    "ask-dp-search",
    "cr-media-carousel",
    "image-block-carousel",
    "sims-simscontainer",
)

PRICE_CONTAINER_SELECTORS = (
    "#corePriceDisplay_desktop_feature_div",
    "#corePrice_feature_div",
    "#corePrice_desktop",
    "#desktop_unifiedPrice",
    "#unifiedPrice_feature_div",
    "#apex_desktop",
    "#buybox",
    "#qualifiedBuybox",
    "#desktop_buybox",
    "#centerCol",
)

PRICE_ELEMENT_SELECTORS = (
    "span.priceToPay span.a-offscreen",
    ".apex-price-to-pay-value span.a-offscreen",
    ".reinventPricePriceToPayMargin span.a-offscreen",
    "span.a-price:not(.a-text-price) span.a-offscreen",
    "#priceblock_ourprice",
    "#priceblock_dealprice",
)


def parse_hidden_price(soup: BeautifulSoup) -> tuple[str | None, str | None]:
    hidden_price = soup.select_one("#priceValue")
    if not hidden_price:
        return None, None

    raw_value = clean_text(hidden_price.get("value"))
    if not is_valid_price_text(raw_value):
        return None, None

    price_text = format_hidden_price(raw_value, soup)
    return price_text, extract_currency(price_text, soup)


def is_excluded_price_element(element) -> bool:
    """Ignore prices from carousels, sponsored widgets, and similar sections."""
    for parent in element.parents:
        if parent.name is None:
            continue
        combined = f"{parent.get('id', '')} {' '.join(parent.get('class') or [])}".lower()
        if any(pattern in combined for pattern in PRICE_EXCLUDE_PATTERNS):
            return True
    return False


def is_valid_price_text(text: str | None) -> bool:
    text = clean_text(text)
    if not text:
        return False

    lowered = text.lower()
    if lowered in {"null", "none"}:
        return False
    if lowered.startswith(("typical:", "list:", "was:", "rrp:", "see price", "from ")):
        return False

    return bool(re.search(r"\d", text))


def price_from_element(element, soup: BeautifulSoup) -> tuple[str | None, str | None]:
    price_text = clean_text(element.get_text())
    if not is_valid_price_text(price_text):
        return None, None
    return price_text, extract_currency(price_text, soup)


def parse_price_from_selected_variation(soup: BeautifulSoup) -> tuple[str | None, str | None]:
    for swatch in soup.select('li[data-asin][data-initiallyselected="true"]'):
        price, currency = parse_swatch_price(swatch)
        if is_valid_price_text(price):
            return price, currency
    return None, None


def parse_dimension_values_display_data(html: str) -> dict[str, list[str]]:
    match = re.search(
        r'"dimensionValuesDisplayData"\s*:\s*(\{.*?\})\s*,\s*"',
        html,
        re.DOTALL,
    )
    if not match:
        return {}

    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return {}

    return {
        asin: [str(value) for value in values]
        for asin, values in data.items()
        if isinstance(values, list)
    }


def extract_amazon_product_slug(url: str) -> str:
    path = re.sub(r"[?#].*$", "", url)
    match = re.search(r"amazon\.[^/]+/([^/]+)/dp/", path, re.IGNORECASE)
    return match.group(1).lower() if match else ""


def is_list_price_element(element) -> bool:
    for parent in element.parents:
        classes = " ".join(parent.get("class") or []).lower()
        if "a-text-price" in classes or "basis-price" in classes:
            return True
    return False


def format_hidden_price(value: str, soup: BeautifulSoup) -> str:
    value = value.strip()
    if re.search(r"[$€£¥]|^[A-Z]{3}\b", value):
        return value

    try:
        amount = float(value.replace(",", ""))
    except ValueError:
        return value

    currency = extract_currency(value, soup) or "$"
    if re.fullmatch(r"[A-Z]{3}", currency):
        return f"{currency}{amount:,.2f}"
    return f"{currency}{amount:,.2f}"


def find_asin_from_url_hint(url: str, dimension_data: dict[str, list[str]]) -> str | None:
    """Match a URL slug to a child ASIN when the landing page defaults elsewhere."""
    if not dimension_data:
        return None

    slug = extract_amazon_product_slug(url)
    if not slug:
        return None

    slug_tokens = {token for token in re.split(r"[^a-z0-9]+", slug) if len(token) >= 3}

    best_asin = None
    best_score = 0
    for asin, labels in dimension_data.items():
        label_text = " ".join(labels).lower()
        label_tokens = {token for token in re.split(r"[^a-z0-9]+", label_text) if len(token) >= 3}
        score = len(slug_tokens & label_tokens)
        if score > best_score:
            best_score = score
            best_asin = asin

    return best_asin if best_score > 0 else None


def fetch_asin_price(asin: str, base_url: str | None = None) -> tuple[str | None, str | None]:
    """Fetch a variant page when the parent listing has no buy-box price."""
    variant_url = asin_to_url(asin, base_url)
    html = fetch_page(variant_url)
    soup = BeautifulSoup(html, "lxml")
    return parse_price(soup)


def parse_price(soup: BeautifulSoup) -> tuple[str | None, str | None]:
    """Return price text and currency if available."""
    for container_selector in PRICE_CONTAINER_SELECTORS:
        container = soup.select_one(container_selector)
        if not container:
            continue

        for price_selector in PRICE_ELEMENT_SELECTORS:
            for element in container.select(price_selector):
                if is_excluded_price_element(element) or is_list_price_element(element):
                    continue
                price, currency = price_from_element(element, soup)
                if price:
                    return price, currency

    price, currency = parse_price_from_selected_variation(soup)
    if price:
        return price, currency

    for price_selector in PRICE_ELEMENT_SELECTORS:
        for element in soup.select(price_selector):
            if is_excluded_price_element(element) or is_list_price_element(element):
                continue
            price, currency = price_from_element(element, soup)
            if price:
                return price, currency

    return parse_hidden_price(soup)


def resolve_product_price(
    soup: BeautifulSoup,
    html: str,
    url: str,
) -> tuple[str | None, str | None]:
    price, currency = parse_price(soup)
    if price:
        return price, currency

    dimension_data = parse_dimension_values_display_data(html)
    hinted_asin = find_asin_from_url_hint(url, dimension_data)
    if hinted_asin:
        return fetch_asin_price(hinted_asin, url)

    landing_match = re.search(r"landingAsin=([A-Z0-9]{10})", html)
    landing_asin = landing_match.group(1) if landing_match else extract_asin(url)
    current_asin = extract_asin(url)
    if landing_asin and landing_asin != current_asin:
        return fetch_asin_price(landing_asin, url)

    return None, None


def normalize_review_count(value: str | None) -> str | None:
    value = clean_text(value)
    if not value:
        return None

    digits_match = re.search(r"([\d,]+)", value)
    if digits_match:
        return digits_match.group(1)

    return value.strip("()").replace("-", "").strip() or None


def parse_rating(soup: BeautifulSoup) -> tuple[str | None, str | None]:
    rating_element = soup.select_one("#acrPopover")
    review_element = soup.select_one("#acrCustomerReviewText")

    rating = None
    if rating_element and rating_element.get("title"):
        rating = clean_text(rating_element["title"])

    review_count = None
    if review_element:
        aria_label = review_element.get("aria-label")
        if aria_label:
            review_count = normalize_review_count(aria_label)
        if not review_count:
            review_count = normalize_review_count(review_element.get_text())

    return rating, review_count


def parse_availability(soup: BeautifulSoup) -> str | None:
    availability_selectors = [
        "#availability span",
        "#availability .a-color-success",
        "#availability .a-color-price",
        "#availability .a-color-state",
        ".primary-availability-message",
    ]

    for selector in availability_selectors:
        element = soup.select_one(selector)
        if element:
            text = clean_text(element.get_text())
            if text:
                return text

    return None


def normalize_description(text: str | None) -> str | None:
    text = clean_text(text)
    if not text:
        return None

    text = re.sub(
        r"^(Product Description|Book Description|Description)\s*:?\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    return clean_text(text)


def parse_description(soup: BeautifulSoup) -> str | None:
    """Extract product or book description text."""
    description_selectors = (
        "#bookDescription_feature_div .a-expander-content",
        "#productDescription_feature_div #productDescription",
        "#productDescription",
        "#bookDescription_feature_div",
        "#productDescription_feature_div",
    )

    for selector in description_selectors:
        element = soup.select_one(selector)
        if not element:
            continue

        text = normalize_description(element.get_text(" ", strip=True))
        if text:
            return text

    return None


def parse_about_the_author(soup: BeautifulSoup) -> tuple[str | None, str | None]:
    """Extract author bio and photo from the books author card section."""
    author_card = soup.select_one("[class*='about-the-author-card_style_cardContentDiv']")
    if not author_card:
        teaser = soup.select_one("#books-entity-teaser")
        if teaser:
            author_card = teaser.find_next(
                "div",
                class_=lambda value: value
                and any("about-the-author-card" in class_name for class_name in value),
            )

    if not author_card:
        return None, None

    image_element = author_card.select_one(
        "img[src*='amzn-author-media'], [class*='authorImage'] img"
    )
    author_image = image_element.get("src") if image_element else None

    content_element = author_card.select_one(
        "[class*='peekableContent'], [class*='cardui-content']"
    )
    author_text = (
        clean_text(content_element.get_text(" ", strip=True))
        if content_element
        else None
    )

    return author_text, author_image


def parse_about_sections(soup: BeautifulSoup) -> dict[str, str]:
    """Extract non-author sections like 'About the Publisher'."""
    sections: dict[str, str] = {}

    for heading in soup.select("h3, h2"):
        title = clean_text(heading.get_text())
        if not title or not title.lower().startswith("about "):
            continue
        if "author" in title.lower():
            continue
        if heading.get("id") == "books-entity-teaser":
            continue

        container = heading.find_parent("div", class_="a-section")
        content_element = container.select_one(".a-padding-small") if container else None
        if not content_element:
            content_element = heading.find_next_sibling("div")

        content = clean_text(
            content_element.get_text(" ", strip=True) if content_element else None
        )
        if title and content and title not in sections:
            sections[title] = content

    return sections


def parse_about_this_item(soup: BeautifulSoup) -> list[str]:
    """Extract 'About this item' bullet points from all Amazon layouts."""
    bullets: list[str] = []
    seen: set[str] = set()

    def add_bullet(text: str | None) -> None:
        text = clean_text(text)
        if not text:
            return
        if text.lower().startswith("make sure this fits"):
            return
        if text in seen:
            return
        seen.add(text)
        bullets.append(text)

    item_selectors = (
        "#feature-bullets ul li span.a-list-item, "
        "#productFactsDesktopExpander ul li span.a-list-item, "
        "#productFacts_feature_div ul li span.a-list-item"
    )
    for item in soup.select(item_selectors):
        add_bullet(item.get_text())

    for title in soup.select("h3.product-facts-title, h1.product-facts-title"):
        list_element = title.find_next_sibling("ul")
        if not list_element:
            parent = title.find_parent(["div", "section"])
            list_element = parent.select_one("ul") if parent else None
        if not list_element:
            continue

        for item in list_element.select("li span.a-list-item, li span.a-color-base"):
            add_bullet(item.get_text())

    return bullets


def normalize_spec_key(key: str) -> str:
    key = re.sub(r"[\s:\u200e\u200f]+", " ", key)
    return re.sub(r"\s+", " ", key).strip()


def add_specification(specs: dict[str, str], key: str | None, value: str | None) -> None:
    key = clean_text(key)
    value = clean_text(value)
    if not key or not value:
        return

    key = normalize_spec_key(key)
    if key.lower() in SKIP_SPEC_KEYS:
        return

    if key not in specs:
        specs[key] = value


def parse_table_specifications(table) -> dict[str, str]:
    specs: dict[str, str] = {}
    for row in table.select("tr"):
        key_element = row.select_one("th")
        value_element = row.select_one("td")
        if not key_element or not value_element:
            continue
        add_specification(
            specs,
            key_element.get_text(),
            value_element.get_text(),
        )
    return specs


def normalize_section_name(name: str) -> str:
    return re.sub(r"\s+", " ", name).strip().title()


def parse_voyager_specification_sections(soup: BeautifulSoup) -> dict[str, dict[str, str]]:
    """Parse the 'See all product specifications' side sheet by section."""
    sections: dict[str, dict[str, str]] = {}

    def store_section(section_name: str | None, section_specs: dict[str, str]) -> None:
        if not section_name or not section_specs:
            return
        normalized_name = normalize_section_name(section_name)
        existing = sections.setdefault(normalized_name, {})
        existing.update(section_specs)

    section_blocks = soup.select(
        "#voyager-ns-desktop-side-sheet-container .voyager-side-sheet-attribute-section, "
        "#voyagerSideSheet_feature_div .voyager-side-sheet-attribute-section"
    )
    for section_block in section_blocks:
        header = section_block.select_one(".voyager-ns-desktop-subsection-name")
        section_name = clean_text(header.get_text()) if header else None

        section_specs: dict[str, str] = {}
        for table in section_block.select("table"):
            section_specs.update(parse_table_specifications(table))

        store_section(section_name, section_specs)

    parsed_section_keys = {
        key.lower().replace(" ", "_")
        for key in sections
    }

    # Hidden lazy-loaded voyager data blocks (skip duplicates already parsed above)
    for data_block in soup.select(".voyager-ns-desktop-data"):
        block_id = (data_block.get("id") or "").lower()
        if block_id and block_id in parsed_section_keys:
            continue

        header = data_block.select_one(".voyager-ns-desktop-subsection-name")
        if header:
            section_name = clean_text(header.get_text())
        elif block_id:
            section_name = clean_text(block_id.replace("_", " ").title())
        else:
            section_name = None

        section_specs: dict[str, str] = {}
        for table in data_block.select("table"):
            section_specs.update(parse_table_specifications(table))

        store_section(section_name, section_specs)

    return sections


def parse_prod_details_specification_sections(soup: BeautifulSoup) -> dict[str, dict[str, str]]:
    """Parse inline product details expander sections (e.g. Processor, Display)."""
    sections: dict[str, dict[str, str]] = {}

    for expander in soup.select(
        "#prodDetails .a-expander-container, "
        "#productDetails_expanderSectionTables .a-expander-container"
    ):
        header = expander.select_one(".a-expander-prompt")
        section_name = clean_text(header.get_text()) if header else None

        section_specs: dict[str, str] = {}
        for table in expander.select("table.prodDetTable, table.a-keyvalue"):
            section_specs.update(parse_table_specifications(table))

        if not section_name or not section_specs:
            continue

        normalized_name = normalize_section_name(section_name)
        sections.setdefault(normalized_name, {}).update(section_specs)

    return sections


def merge_specification_sections(sections: dict[str, dict[str, str]]) -> dict[str, str]:
    merged: dict[str, str] = {}
    for section_specs in sections.values():
        for key, value in section_specs.items():
            add_specification(merged, key, value)
    return merged


def parse_specifications(soup: BeautifulSoup) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    """Extract all product key-value specs from any Amazon page layout."""
    specs: dict[str, str] = {}
    sections = parse_voyager_specification_sections(soup)
    for section_name, section_specs in parse_prod_details_specification_sections(soup).items():
        sections.setdefault(section_name, {}).update(section_specs)

    # Product overview table (electronics, appliances, etc.)
    for row in soup.select("#productOverview_feature_div tr[role='listitem']"):
        key_element = row.select_one("td:first-child span.a-text-bold, td:first-child")
        value_element = row.select_one(
            "td:last-child span.po-break-word, td:last-child span.a-size-base, td:last-child"
        )
        add_specification(
            specs,
            key_element.get_text() if key_element else None,
            value_element.get_text() if value_element else None,
        )

    # Top highlights / product facts (apparel, etc.)
    for block in soup.select(".product-facts-detail"):
        key_element = block.select_one(".a-col-left, .a-fixed-left-grid-col:first-child")
        value_element = block.select_one(".a-col-right, .a-fixed-left-grid-col:last-child")
        add_specification(
            specs,
            key_element.get_text() if key_element else None,
            value_element.get_text() if value_element else None,
        )

    # Product details bullet list
    for item in soup.select("#detailBullets_feature_div li"):
        key_element = item.select_one("span.a-text-bold")
        if not key_element:
            continue

        value_element = key_element.find_next_sibling("span")
        if not value_element:
            continue

        add_specification(
            specs,
            key_element.get_text(),
            value_element.get_text(),
        )

    # Other specification tables not already covered by sectioned layouts
    table_selectors = (
        "#productDetails table, "
        "#productDetails_feature_div table, "
        "#productDetailsNonPets_feature_div table"
    )
    for table in soup.select(table_selectors):
        for key, value in parse_table_specifications(table).items():
            add_specification(specs, key, value)

    # Voyager side sheet is usually the most complete source
    for key, value in merge_specification_sections(sections).items():
        add_specification(specs, key, value)

    return specs, sections


def is_book_byline(text: str) -> bool:
    """Detect Amazon book author/byline text that is not a product brand."""
    book_patterns = (
        r"\(Author\)",
        r"\(Illustrator\)",
        r"\bFormat\s*:",
        r"^by\s+",
    )
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in book_patterns)


def asin_to_url(asin: str, base_url: str | None = None) -> str:
    """Build an Amazon product URL for a given ASIN."""
    domain = "https://www.amazon.com"
    if base_url:
        match = re.search(r"(https?://[^/]+)", base_url)
        if match:
            domain = match.group(1)
    return f"{domain}/dp/{asin.upper()}"


def humanize_dimension_id(dimension_id: str) -> str:
    name = dimension_id.replace("_name", "").replace("_", " ").strip()
    return name.title() if name else dimension_id


def parse_swatch_price(swatch: BeautifulSoup) -> tuple[str | None, str | None]:
    price_element = swatch.select_one(
        ".apex-pricetopay-value [aria-hidden='true'], "
        ".apex-pricetopay-value span[aria-hidden='true']"
    )
    if price_element:
        price_text = clean_text(price_element.get_text())
        if price_text:
            return price_text, extract_currency(price_text)

    accessibility = swatch.select_one(".apex-pricetopay-accessibility-label")
    if accessibility:
        label_text = accessibility.get("data-pricetopay-label") or accessibility.get_text()
        match = re.search(r"[$€£¥][\d,.]+", label_text or "")
        if match:
            price_text = match.group(0)
            return price_text, extract_currency(price_text)

    return None, None


def parse_swatch_availability(swatch: BeautifulSoup) -> str | None:
    availability = swatch.select_one("#twisterAvailability, .twisterAvailability")
    if availability:
        return clean_text(availability.get_text())

    unavailable = swatch.select_one(".default-slot-unavailable")
    if unavailable:
        return clean_text(unavailable.get_text())

    return None


def parse_dimension_label(soup: BeautifulSoup, dimension_id: str) -> str:
    label_element = soup.select_one(
        f"#inline-twister-row-{dimension_id} .a-form-label, "
        f"#inline-twister-dim-title-{dimension_id}"
    )
    if label_element:
        label = clean_text(label_element.get_text())
        if label:
            return label.rstrip(":")

    aria_label = soup.select_one(f"#dim-values-aria-label-{dimension_id}")
    if aria_label:
        label_text = clean_text(aria_label.get_text()) or ""
        match = re.search(r"Make a (.+?) selection", label_text, re.IGNORECASE)
        if match:
            return match.group(1)

    return humanize_dimension_id(dimension_id)


def parse_inline_twister_option(swatch: BeautifulSoup) -> dict | None:
    asin = swatch.get("data-asin")
    if not asin:
        return None

    image_element = swatch.select_one("img.swatch-image")
    text_element = swatch.select_one(".swatch-title-text-display")

    label = None
    image = None
    if image_element:
        label = clean_text(image_element.get("alt"))
        image = image_element.get("src")
    if text_element:
        label = clean_text(text_element.get_text()) or label
    if not label:
        label = asin

    price, currency = parse_swatch_price(swatch)
    availability = parse_swatch_availability(swatch)
    unavailable = swatch.get("data-initiallyunavailable", "").lower() == "true"
    if availability and "unavailable" in availability.lower():
        unavailable = True

    style = swatch.get("style", "")
    hidden = (
        swatch.get("data-collapsed-view", "").lower() == "false"
        or "display: none" in style.replace(" ", "")
        or "display:none" in style.replace(" ", "")
    )

    return {
        "asin": asin.upper(),
        "label": label,
        "image": image,
        "price": price,
        "currency": currency,
        "availability": availability,
        "selected": swatch.get("data-initiallyselected", "").lower() == "true",
        "unavailable": unavailable,
        "hidden": hidden,
    }


def parse_variations(soup: BeautifulSoup) -> list[dict]:
    """Extract Amazon twister variation dimensions and their options."""
    variations: list[dict] = []
    seen_dimensions: set[str] = set()

    for container in soup.select('[id^="inline-twister-expander-content-"]'):
        dimension_id = container.get("id", "").replace("inline-twister-expander-content-", "")
        if not dimension_id or dimension_id in seen_dimensions:
            continue

        options: list[dict] = []
        for swatch in container.select("li[data-asin]"):
            option = parse_inline_twister_option(swatch)
            if option:
                options.append(option)

        if not options:
            continue

        seen_dimensions.add(dimension_id)
        display_type = "image" if any(option.get("image") for option in options) else "text"
        variations.append(
            {
                "id": dimension_id,
                "label": parse_dimension_label(soup, dimension_id),
                "type": display_type,
                "has_hidden_options": any(option.get("hidden") for option in options),
                "options": options,
            }
        )

    # Classic twister fallback (older layout)
    for row in soup.select('[id^="variation_"]'):
        dimension_id = row.get("id", "").replace("variation_", "")
        if not dimension_id or dimension_id in seen_dimensions:
            continue

        if row.select_one("select[id^='native_dropdown_selected_']"):
            continue

        options = []
        for swatch in row.select("li[data-defaultasin], li[data-asin]"):
            asin = swatch.get("data-defaultasin") or swatch.get("data-asin")
            if not asin:
                continue

            image_element = swatch.select_one("img")
            text_element = swatch.select_one(".twisterTextDiv, .a-button-text")
            label = clean_text(image_element.get("alt") if image_element else None)
            if not label and text_element:
                label = clean_text(text_element.get_text())
            if not label:
                label = asin

            options.append(
                {
                    "asin": asin.upper(),
                    "label": label,
                    "image": image_element.get("src") if image_element else None,
                    "price": None,
                    "currency": None,
                    "availability": None,
                    "selected": "swatchSelect" in swatch.get("class", [])
                    or "a-button-selected" in " ".join(swatch.get("class", [])),
                    "unavailable": "a-button-unavailable" in " ".join(swatch.get("class", [])),
                    "hidden": False,
                }
            )

        if not options:
            continue

        seen_dimensions.add(dimension_id)
        display_type = "image" if any(option.get("image") for option in options) else "text"
        variations.append(
            {
                "id": dimension_id,
                "label": parse_dimension_label(soup, dimension_id),
                "type": display_type,
                "has_hidden_options": any(option.get("hidden") for option in options),
                "options": options,
            }
        )

    variations.extend(parse_dropdown_variations(soup, seen_dimensions))
    return variations


def parse_dropdown_dimension_id(select_element) -> str:
    select_id = select_element.get("id", "")
    match = re.search(r"native_dropdown_selected_(.+)", select_id)
    if match:
        return match.group(1)

    select_name = select_element.get("name", "")
    match = re.search(r"dropdown_selected_(.+)", select_name)
    if match:
        return match.group(1)

    return "variation"


def parse_dropdown_dimension_label(
    soup: BeautifulSoup,
    select_element,
    dimension_id: str,
) -> str:
    variation_row = soup.select_one(f"#variation_{dimension_id}")
    if variation_row:
        label_element = variation_row.select_one(".a-form-label")
        if label_element:
            label = clean_text(label_element.get_text())
            if label:
                return label.rstrip(":")

    touch_header = select_element.get("data-a-touch-header")
    if touch_header:
        return touch_header

    return humanize_dimension_id(dimension_id)


def parse_dropdown_option(option_element) -> dict | None:
    value = option_element.get("value", "")
    if not value or value == "-1":
        return None

    parts = value.split(",", 1)
    if len(parts) != 2:
        return None

    asin = parts[1].strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{10}", asin):
        return None

    label = clean_text(option_element.get("data-a-html-content")) or clean_text(
        option_element.get_text()
    )
    if not label:
        label = asin

    classes = " ".join(option_element.get("class", [])).lower()
    unavailable = "dropdownunavailable" in classes
    selected = option_element.has_attr("selected") or "dropdownselect" in classes

    return {
        "asin": asin,
        "label": label,
        "image": None,
        "price": None,
        "currency": None,
        "availability": "Unavailable" if unavailable else None,
        "selected": selected,
        "unavailable": unavailable,
        "hidden": False,
    }


def parse_dropdown_variations(soup: BeautifulSoup, seen_dimensions: set[str]) -> list[dict]:
    variations: list[dict] = []

    for select in soup.select("select[id^='native_dropdown_selected_']"):
        dimension_id = parse_dropdown_dimension_id(select)
        if dimension_id in seen_dimensions:
            continue

        options: list[dict] = []
        for option in select.select("option"):
            parsed = parse_dropdown_option(option)
            if parsed:
                options.append(parsed)

        if not options:
            continue

        seen_dimensions.add(dimension_id)
        variations.append(
            {
                "id": dimension_id,
                "label": parse_dropdown_dimension_label(soup, select, dimension_id),
                "type": "dropdown",
                "has_hidden_options": False,
                "options": options,
            }
        )

    return variations


def extract_brand(specifications: dict[str, str], soup: BeautifulSoup) -> str | None:
    for key in ("Brand", "Brand Name", "Manufacturer", "Publisher"):
        if key in specifications:
            return specifications[key]

    brand_element = soup.select_one("#bylineInfo")
    if brand_element:
        brand_text = clean_text(brand_element.get_text())
        if brand_text:
            brand_text = re.sub(
                r"^(Visit the|Brand:|Store:)\s*",
                "",
                brand_text,
                flags=re.IGNORECASE,
            )
            brand_text = re.sub(r"\s+Store$", "", brand_text, flags=re.IGNORECASE)
            brand_text = clean_text(brand_text)
            if brand_text and not is_book_byline(brand_text):
                return brand_text

    return None


def parse_images(soup: BeautifulSoup) -> list[str]:
    """Extract all product images, preferring full-resolution hiRes URLs."""
    images: list[str] = []
    seen: set[str] = set()

    def add_image(url: str | None) -> None:
        if not url or url in seen:
            return
        seen.add(url)
        images.append(url)

    # Primary source: colorImages JSON embedded in page scripts (all images)
    for script in soup.find_all("script"):
        text = script.string or ""
        if "colorImages" not in text or "hiRes" not in text:
            continue

        match = re.search(
            r"['\"]colorImages['\"]\s*:\s*\{[^[]*['\"]initial['\"]\s*:\s*(\[.*?\])\s*[,}]",
            text,
            re.DOTALL,
        )
        if match:
            try:
                for item in json.loads(match.group(1)):
                    add_image(item.get("hiRes") or item.get("large"))
                if images:
                    return images
            except json.JSONDecodeError:
                pass

        for url in re.findall(r'"hiRes"\s*:\s*"(https://[^"]+)"', text):
            add_image(url)
        if images:
            return images

    # Fallback: high-res URLs from the main image carousel
    for image in soup.select(".desktop-media-mainView img[data-old-hires]"):
        add_image(image.get("data-old-hires"))

    if images:
        return images

    # Last resort: thumbnail images from the sidebar
    for image in soup.select("#altImages img, #landingImage"):
        src = image.get("src")
        if not src:
            continue
        add_image(re.sub(r"\._[A-Z0-9_,]+_\.", ".", src))

    return images


def parse_product_page(html: str, url: str) -> ProductData:
    soup = BeautifulSoup(html, "lxml")
    price, currency = resolve_product_price(soup, html, url)
    rating, review_count = parse_rating(soup)
    specifications, specification_sections = parse_specifications(soup)

    about_this_item = parse_about_this_item(soup)
    about_the_author, author_image = parse_about_the_author(soup)

    title_element = soup.select_one("#productTitle")

    return ProductData(
        url=url,
        store="amazon",
        asin=extract_asin(url) or soup.select_one("[data-asin]") and soup.select_one("[data-asin]").get("data-asin"),
        title=clean_text(title_element.get_text()) if title_element else None,
        price=price,
        currency=currency,
        rating=rating,
        review_count=review_count,
        availability=parse_availability(soup),
        brand=extract_brand(specifications, soup),
        about_this_item=about_this_item,
        feature_bullets=about_this_item,
        description=parse_description(soup),
        about_the_author=about_the_author,
        author_image=author_image,
        about_sections=parse_about_sections(soup),
        specifications=specifications,
        specification_sections=specification_sections,
        images=parse_images(soup),
        variations=parse_variations(soup),
    )


def scrape_amazon_product(url: str) -> ProductData:
    """Scrape a single Amazon product page."""
    canonical_url = url.split("?")[0]
    html = fetch_page(url)
    return parse_product_page(html, canonical_url)


CORE_FIELDS = (
    "url",
    "asin",
    "title",
    "price",
    "currency",
    "rating",
    "review_count",
    "availability",
    "brand",
)


def product_to_dict(product: ProductData) -> dict:
    return asdict(product)


def build_csv_content(product: ProductData) -> str:
    """Build CSV content in memory for download."""
    row: dict[str, str] = {
        field_name: getattr(product, field_name) or "" for field_name in CORE_FIELDS
    }

    for key, value in product.specifications.items():
        row[key] = value

    if product.description:
        row["description"] = product.description
    if product.about_the_author:
        row["about_the_author"] = product.about_the_author
    if product.author_image:
        row["author_image"] = product.author_image

    for section_title, section_text in product.about_sections.items():
        row[section_title] = section_text

    if product.about_this_item:
        row["about_this_item"] = " ".join(product.about_this_item)
    if product.images:
        row["images"] = '", "'.join(product.images)

    fieldnames = list(CORE_FIELDS)
    for key in product.specifications:
        if key not in fieldnames:
            fieldnames.append(key)

    if product.description:
        fieldnames.append("description")
    if product.about_the_author:
        fieldnames.append("about_the_author")
    if product.author_image:
        fieldnames.append("author_image")
    fieldnames.extend(product.about_sections.keys())

    if product.about_this_item:
        fieldnames.append("about_this_item")
    if product.images:
        fieldnames.append("images")

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerow(row)
    return buffer.getvalue()
