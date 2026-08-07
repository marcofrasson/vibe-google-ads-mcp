# Copyright 2026 Vibe Digital.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Keyword planning tools for the Google Ads MCP server.

These are read-only: they ask the API for keyword ideas and search volumes and
change nothing in the account. They cover the "Keyword Planning Services"
capability declared in our Basic Access application.

Policy note, from that same application: Keyword Planner data is for internal
use only. It must not be shown to clients, published in a client-facing report
or dashboard, or resold. Exposing it to third parties would require Required
Minimum Functionality compliance, which this server does not implement.
"""

from typing import Any, Dict, List

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from google.ads.googleads.errors import GoogleAdsException

import ads_mcp.utils as utils

planning_mcp = FastMCP("planning")

_READ_ONLY = ToolAnnotations(readOnlyHint=True, idempotentHint=True)

# Defaults for a Brazilian account: Brazil, in Portuguese.
_DEFAULT_GEO_TARGET_IDS = ["2076"]  # geoTargetConstants/2076 = Brazil
_DEFAULT_LANGUAGE_ID = "1014"  # languageConstants/1014 = Portuguese

_MAX_SEED_KEYWORDS = 20  # API limit for KeywordSeed
_MAX_RESULTS = 200


def _normalize_customer_id(customer_id: str) -> str:
    """Accepts 123-456-7890 or 1234567890 and returns digits only."""
    digits = "".join(ch for ch in str(customer_id) if ch.isdigit())
    if len(digits) != 10:
        raise ToolError(
            f"customer_id must have 10 digits, got '{customer_id}'."
        )
    return digits


def _resource_name(value: str, collection: str) -> str:
    """Turns '2076' into 'geoTargetConstants/2076', leaving full names alone."""
    text = str(value).strip()
    if "/" in text:
        return text
    if not text.isdigit():
        raise ToolError(
            f"'{text}' is not a valid {collection} id; pass digits only "
            f"(e.g. 2076) or the full resource name."
        )
    return f"{collection}/{text}"


def _micros_to_currency(micros: int | None) -> float | None:
    """1_000_000 micros = one unit of the account currency."""
    if not micros:
        return None
    return round(micros / 1_000_000, 2)


def _raise_google_ads_error(ex: GoogleAdsException) -> None:
    error_msgs = [
        f"Google Ads API Error: {error.message}" for error in ex.failure.errors
    ]
    raise ToolError(f"Request ID: {ex.request_id}\n" + "\n".join(error_msgs))


@planning_mcp.tool(annotations=_READ_ONLY)
def generate_keyword_ideas(
    customer_id: str,
    keywords: List[str] | None = None,
    page_url: str | None = None,
    geo_target_ids: List[str] | None = None,
    language_id: str = _DEFAULT_LANGUAGE_ID,
    keyword_plan_network: str = "GOOGLE_SEARCH",
    include_adult_keywords: bool = False,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Returns keyword ideas with monthly search volume, competition and bid ranges.

    Read-only: nothing is created or changed in the account. Use it to size
    demand for a product or segment before planning a Search campaign.

    Provide `keywords`, `page_url`, or both (both together gives the API a page
    to read plus the seeds to anchor on, which is usually the best input).

    Args:
        customer_id: The id of the customer.
        keywords: Up to 20 seed terms, e.g. ["tubo de pvc", "conexoes hidraulicas"].
        page_url: A page whose content seeds the ideas, e.g. a product page.
        geo_target_ids: Geo target constant ids. Defaults to ["2076"] (Brazil).
            Use suggest_geo_targets to look up a state or city id.
        language_id: Language constant id. Defaults to "1014" (Portuguese).
        keyword_plan_network: GOOGLE_SEARCH or GOOGLE_SEARCH_AND_PARTNERS.
        include_adult_keywords: Whether to include adult keywords.
        limit: Maximum ideas to return (1-200).

    Returns:
        One entry per idea, ordered by average monthly searches, with:
        keyword, avg_monthly_searches, competition, competition_index (0-100),
        low_top_of_page_bid, high_top_of_page_bid and average_cpc, the last
        three already converted from micros to the account currency.
    """
    customer_id = _normalize_customer_id(customer_id)

    seeds = [k.strip() for k in (keywords or []) if k and k.strip()]
    url = (page_url or "").strip()
    if not seeds and not url:
        raise ToolError(
            "Provide at least one of `keywords` or `page_url` to seed the ideas."
        )
    if len(seeds) > _MAX_SEED_KEYWORDS:
        raise ToolError(
            f"`keywords` accepts at most {_MAX_SEED_KEYWORDS} seed terms, "
            f"got {len(seeds)}."
        )
    if not 1 <= limit <= _MAX_RESULTS:
        raise ToolError(f"`limit` must be between 1 and {_MAX_RESULTS}.")

    client = utils.get_googleads_client()
    service = utils.get_googleads_service("KeywordPlanIdeaService")

    request = client.get_type("GenerateKeywordIdeasRequest")
    request.customer_id = customer_id
    request.include_adult_keywords = include_adult_keywords
    request.language = _resource_name(language_id, "languageConstants")
    request.geo_target_constants.extend(
        _resource_name(geo, "geoTargetConstants")
        for geo in (geo_target_ids or _DEFAULT_GEO_TARGET_IDS)
    )

    network_enum = client.enums.KeywordPlanNetworkEnum
    try:
        request.keyword_plan_network = getattr(
            network_enum, keyword_plan_network.upper()
        )
    except AttributeError:
        valid = ", ".join(
            v.name
            for v in network_enum
            if v.name not in ("UNSPECIFIED", "UNKNOWN")
        )
        raise ToolError(
            f"keyword_plan_network must be one of: {valid} "
            f"(got '{keyword_plan_network}')."
        )

    # Exactly one seed field may be set on the request.
    if seeds and url:
        request.keyword_and_url_seed.url = url
        request.keyword_and_url_seed.keywords.extend(seeds)
    elif seeds:
        request.keyword_seed.keywords.extend(seeds)
    else:
        request.url_seed.url = url

    try:
        response = service.generate_keyword_ideas(request=request)
    except GoogleAdsException as ex:
        _raise_google_ads_error(ex)

    ideas: List[Dict[str, Any]] = []
    for result in response:
        metrics = result.keyword_idea_metrics
        ideas.append(
            {
                "keyword": result.text,
                "avg_monthly_searches": metrics.avg_monthly_searches or 0,
                "competition": metrics.competition.name,
                "competition_index": metrics.competition_index or 0,
                "low_top_of_page_bid": _micros_to_currency(
                    metrics.low_top_of_page_bid_micros
                ),
                "high_top_of_page_bid": _micros_to_currency(
                    metrics.high_top_of_page_bid_micros
                ),
                "average_cpc": _micros_to_currency(metrics.average_cpc_micros),
            }
        )

    ideas.sort(key=lambda item: item["avg_monthly_searches"], reverse=True)
    return ideas[:limit]


@planning_mcp.tool(annotations=_READ_ONLY)
def suggest_geo_targets(
    location_names: List[str],
    locale: str = "pt",
    country_code: str = "BR",
) -> List[Dict[str, Any]]:
    """Looks up geo target constant ids by name, for use in generate_keyword_ideas.

    Read-only, and it does not need a customer_id: geo target constants are
    global API data, not account data.

    Args:
        location_names: Names to look up, e.g. ["Joinville", "Caxias do Sul"].
        locale: Locale for the returned names. Defaults to "pt".
        country_code: Restricts the search to one country. Defaults to "BR".

    Returns:
        One entry per match, with id, name, canonical_name (e.g.
        "Joinville, Santa Catarina, Brazil"), target_type (City, State,
        Country...), country_code and reach.
    """
    names = [n.strip() for n in location_names if n and n.strip()]
    if not names:
        raise ToolError("`location_names` must contain at least one name.")

    client = utils.get_googleads_client()
    service = utils.get_googleads_service("GeoTargetConstantService")

    request = client.get_type("SuggestGeoTargetConstantsRequest")
    request.locale = locale
    request.country_code = country_code
    request.location_names.names.extend(names)

    try:
        response = service.suggest_geo_target_constants(request=request)
    except GoogleAdsException as ex:
        _raise_google_ads_error(ex)

    return [
        {
            "id": str(suggestion.geo_target_constant.id),
            "name": suggestion.geo_target_constant.name,
            "canonical_name": suggestion.geo_target_constant.canonical_name,
            "target_type": suggestion.geo_target_constant.target_type,
            "country_code": suggestion.geo_target_constant.country_code,
            "reach": suggestion.reach,
        }
        for suggestion in response.geo_target_constant_suggestions
    ]
