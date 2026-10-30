
from __future__ import annotations

import json
import re
import time
from typing import Any
from urllib.parse import quote

from curl_cffi import requests


BASE_URL = "https://www.alibaba.com"
HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "accept-language": "en-US,en;q=0.9",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}

_last_request = 0.0


def _get(url: str) -> str:
    global _last_request

    wait = 3 - (time.time() - _last_request)
    if wait > 0:
        time.sleep(wait)

    response = requests.get(
        url,
        headers=HEADERS,
        impersonate="chrome",
        timeout=25,
    )
    _last_request = time.time()

    if response.status_code in (403, 429):
        raise RuntimeError(
            f"Alibaba blocked the request (HTTP {response.status_code}). "
            "Try again later."
        )

    response.raise_for_status()
    html = response.text

    if any(
        marker in html.lower()
        for marker in (
            "captcha",
            "verify you are human",
            "security verification",
        )
    ):
        raise RuntimeError("Alibaba returned an anti-bot verification page.")

    return html


def _extract_json(html: str, marker: str) -> dict[str, Any] | None:
    position = html.find(marker)
    if position == -1:
        return None

    start = html.find("{", position + len(marker))
    if start == -1:
        return None

    depth = 0
    in_string = False
    escaped = False

    for index in range(start, len(html)):
        char = html[index]

        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(html[start:index + 1])
                except json.JSONDecodeError:
                    return None

    return None


def _walk_offers(value: Any):
    if isinstance(value, dict):
        if any(
            key in value
            for key in ("offerId", "offer_id", "productId")
        ):
            yield value

        for child in value.values():
            yield from _walk_offers(child)

    elif isinstance(value, list):
        for child in value:
            yield from _walk_offers(child)


def _clean_offer(item: dict[str, Any]) -> dict[str, Any]:
    product_id = (
        item.get("offerId")
        or item.get("offer_id")
        or item.get("productId")
    )

    title = (
        item.get("title")
        or item.get("subject")
        or item.get("productTitle")
        or item.get("name")
    )

    price = item.get("price") or item.get("priceInfo") or {}
    if isinstance(price, dict):
        price = (
            price.get("price")
            or price.get("formattedPrice")
            or price.get("minPrice")
        )

    if isinstance(price, dict):
        price = price.get("formattedPrice") or price.get("value")

    image = (
        item.get("image")
        or item.get("imageUrl")
        or item.get("productImage")
    )

    if isinstance(image, dict):
        image = image.get("url") or image.get("src")

    url = (
        item.get("productUrl")
        or item.get("detailUrl")
        or item.get("url")
    )

    if not url and product_id:
        url = f"{BASE_URL}/product-detail/{product_id}.html"

    return {
        "id": str(product_id) if product_id else None,
        "title": title,
        "price": price,
        "image": image,
        "url": url,
        "supplier": (
            item.get("companyName")
            or item.get("supplierName")
            or item.get("sellerName")
        ),
        "moq": (
            item.get("minOrder")
            or item.get("minOrderQuantity")
            or item.get("moq")
        ),
    }


def search_alibaba(
    query: str,
    page: int = 1,
    limit: int = 10,
) -> dict[str, Any]:
    """Search Alibaba.com B2B product listings."""

    if not query.strip():
        raise ValueError("Search query cannot be empty.")

    page = max(1, int(page))
    limit = min(30, max(1, int(limit)))

    url = (
        f"{BASE_URL}/trade/search"
        f"?SearchText={quote(query)}&page={page}"
    )

    html = _get(url)

    data = _extract_json(
        html,
        "window.__page__data_sse10",
    )

    if not data:
        data = _extract_json(html, "__page__data_sse10")

    if not data:
        raise RuntimeError(
            "Could not find Alibaba search data. "
            "The website structure may have changed."
        )

    offers = []
    seen = set()

    for item in _walk_offers(data):
        product = _clean_offer(item)

        if not product["id"] or product["id"] in seen:
            continue

        seen.add(product["id"])
        offers.append(product)

        if len(offers) >= limit:
            break

    return {
        "platform": "Alibaba.com",
        "query": query,
        "page": page,
        "count": len(offers),
        "products": offers,
    }


def get_alibaba_product(product: str) -> dict[str, Any]:
    """Retrieve available details for one Alibaba product."""

    if not product.strip():
        raise ValueError("Product ID or URL cannot be empty.")

    if product.startswith("http://") or product.startswith("https://"):
        url = product
    else:
        product_id = re.sub(r"\D", "", product)
        if not product_id:
            raise ValueError("Invalid Alibaba product ID.")
        url = f"{BASE_URL}/product-detail/{product_id}.html"

    html = _get(url)

    data = _extract_json(html, "window.detailData")

    if not data:
        data = _extract_json(html, "window.__page__data_sse10")

    if not data:
        raise RuntimeError(
            "Could not retrieve product details. "
            "Alibaba may have changed its page structure "
            "or restricted this request."
        )

    return {
        "platform": "Alibaba.com",
        "url": url,
        "details": data,
        "note": (
            "Raw page data is returned because Alibaba's product "
            "page structure may vary between listings."
        ),
}
  
