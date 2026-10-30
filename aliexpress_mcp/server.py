"""MCP server exposing AliExpress and Alibaba product search."""

from __future__ import annotations

import logging
import os
from typing import Annotated, Any, Optional

from fastmcp import FastMCP
from fastmcp.server.auth import StaticTokenVerifier
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from aliexpress_mcp.aliexpress_client import (
    CURRENCY,
    LOCALE,
    REGION,
    get_product,
    liveness,
    search,
)

from aliexpress_mcp.alibaba_client import (
    search_alibaba as alibaba_search,
    get_alibaba_product as alibaba_get_product,
)

log = logging.getLogger("aliexpress-mcp")


def _coerce_int(
    value: str | int | None, field: str, *, ge: int | None = None
) -> int | None:
    if value is None or isinstance(value, int):
        result = value
    elif isinstance(value, str) and value.strip():
        try:
            result = int(value.strip())
        except ValueError as exc:
            raise ValueError(
                f"{field} must be an integer, got {value!r}"
            ) from exc
    else:
        raise ValueError(f"{field} must be an integer, got {value!r}")

    if ge is not None and result is not None and result < ge:
        raise ValueError(f"{field} must be >= {ge}, got {result}")

    return result


def _coerce_float(
    value: str | float | None, field: str, *, ge: float | None = None
) -> float | None:
    if value is None or isinstance(value, (int, float)):
        result = float(value) if value is not None else None
    elif isinstance(value, str) and value.strip():
        try:
            result = float(value.strip())
        except ValueError as exc:
            raise ValueError(
                f"{field} must be a number, got {value!r}"
            ) from exc
    else:
        raise ValueError(f"{field} must be a number, got {value!r}")

    if ge is not None and result is not None and result < ge:
        raise ValueError(f"{field} must be >= {ge}, got {result}")

    return result


def _resolve_limit(
    limit: str | int | None,
    max_results: str | int | None,
) -> int:
    if limit is not None and max_results is not None:
        resolved = _coerce_int(limit, "limit", ge=1)
        alias = _coerce_int(max_results, "max_results", ge=1)

        if resolved != alias:
            raise ValueError(
                "limit and max_results have different values"
            )
    elif max_results is not None:
        resolved = _coerce_int(max_results, "max_results", ge=1)
    else:
        resolved = _coerce_int(limit, "limit", ge=1)

    return min(resolved or 10, 60)


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

auth_token = os.environ.get("MCP_AUTH_TOKEN")

if not auth_token:
    raise RuntimeError(
        "MCP_AUTH_TOKEN environment variable is required"
    )

auth = StaticTokenVerifier(
    tokens={
        auth_token: {
            "sub": "admin",
            "client_id": "aliexpress-client",
            "scopes": [],
        }
    }
)

mcp = FastMCP(
    name="aliexpress",
    version="0.4.0",
    auth=auth,
    instructions=(
        "This MCP server provides read-only product search and product "
        "details from AliExpress and Alibaba.com. "
        "Use search_aliexpress and get_aliexpress_product for AliExpress. "
        "Use search_alibaba and get_alibaba_product for Alibaba.com. "
        "Alibaba is a B2B marketplace and product prices, minimum order "
        "quantities and supplier details may vary. "
        "The tools cannot purchase products, send RFQs or contact suppliers. "
        "Both websites may block automated requests or change their page "
        "structures, so some requests may fail."
    ),
)


# ---------------------------------------------------------------------------
# AliExpress tools
# ---------------------------------------------------------------------------

@mcp.tool
def search_aliexpress(
    query: Annotated[
        str,
        Field(description="AliExpress search keywords"),
    ],
    limit: Annotated[
        str | int | None,
        Field(description="Maximum results to return"),
    ] = None,
    sort: Annotated[
        Optional[str],
        Field(
            description=(
                "Sort order: orders, price_asc, price_desc, newest"
            ),
        ),
    ] = None,
    min_price: Annotated[
        str | float | None,
        Field(description=f"Minimum price in {CURRENCY}"),
    ] = None,
    max_price: Annotated[
        str | float | None,
        Field(description=f"Maximum price in {CURRENCY}"),
    ] = None,
    page: Annotated[
        str | int,
        Field(description="Result page, starting from 1"),
    ] = 1,
    max_results: Annotated[
        str | int | None,
        Field(description="Alternative name for limit"),
    ] = None,
) -> dict[str, Any]:
    """Search AliExpress product listings."""

    resolved_limit = _resolve_limit(limit, max_results)
    resolved_page = _coerce_int(page, "page", ge=1) or 1
    resolved_min = _coerce_float(min_price, "min_price", ge=0)
    resolved_max = _coerce_float(max_price, "max_price", ge=0)

    return search(
        query=query,
        limit=resolved_limit,
        sort=sort,
        min_price=resolved_min,
        max_price=resolved_max,
        page=resolved_page,
    )


@mcp.tool
def get_aliexpress_product(
    product: Annotated[
        str,
        Field(
            description=(
                "AliExpress product ID or full product URL"
            ),
        ),
    ],
) -> dict[str, Any]:
    """Retrieve details for an AliExpress product."""

    return get_product(product)


# ---------------------------------------------------------------------------
# Alibaba tools
# ---------------------------------------------------------------------------

@mcp.tool
def search_alibaba(
    query: Annotated[
        str,
        Field(
            description=(
                "Alibaba.com B2B product search keywords, "
                "for example: coffee machine, packaging machine"
            ),
        ),
    ],
    page: Annotated[
        str | int,
        Field(description="Result page, starting from 1"),
    ] = 1,
    limit: Annotated[
        str | int,
        Field(description="Maximum number of results, up to 30"),
    ] = 10,
) -> dict[str, Any]:
    """Search Alibaba.com B2B product listings."""

    resolved_page = _coerce_int(page, "page", ge=1) or 1
    resolved_limit = _coerce_int(limit, "limit", ge=1) or 10

    return alibaba_search(
        query=query,
        page=resolved_page,
        limit=min(resolved_limit, 30),
    )


@mcp.tool
def get_alibaba_product(
    product: Annotated[
        str,
        Field(
            description=(
                "Alibaba product ID or full product URL"
            ),
        ),
    ],
) -> dict[str, Any]:
    """Retrieve available details for an Alibaba.com product."""

    return alibaba_get_product(product)


# ---------------------------------------------------------------------------
# Health check and transport
# ---------------------------------------------------------------------------

@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(_: Request) -> JSONResponse:
    """Container readiness probe."""

    return JSONResponse(liveness())


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    log.info(
        "AliExpress and Alibaba MCP server starting "
        "(market=%s currency=%s locale=%s)",
        REGION,
        CURRENCY,
        LOCALE,
    )

    transport = os.getenv("MCP_TRANSPORT", "http")

    if transport == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.run(
            transport="http",
            host=os.getenv("MCP_HOST", "0.0.0.0"),
            port=int(os.getenv("MCP_PORT", "8000")),
            path=os.getenv("MCP_PATH", "/mcp"),
        )


if __name__ == "__main__":
    main()
        
