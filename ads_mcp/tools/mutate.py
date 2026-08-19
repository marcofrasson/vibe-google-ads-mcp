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

"""Write (mutate) tools for the Google Ads MCP server.

Every tool here follows the same two-step contract that was declared in our
Basic Access application: a call without `confirm=True` is sent to the API with
`validate_only=True`, so the API validates the whole operation and changes
nothing. Only a second, explicit call with `confirm=True` commits it.
"""

import re
from datetime import date
from typing import Any, Dict, List

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from google.ads.googleads.errors import GoogleAdsException
from google.api_core import protobuf_helpers

import ads_mcp.utils as utils

mutate_mcp = FastMCP("mutate")

# Writes are neither read-only nor idempotent. `destructiveHint` is set only on
# the tools that pause/remove existing entities.
_WRITE = ToolAnnotations(readOnlyHint=False, idempotentHint=False)
_DESTRUCTIVE = ToolAnnotations(
    readOnlyHint=False, idempotentHint=False, destructiveHint=True
)

_RSA_HEADLINE_MAX = 30
_RSA_DESCRIPTION_MAX = 90
_ENTITY_STATUSES = ("ENABLED", "PAUSED", "REMOVED")


def _normalize_customer_id(customer_id: str) -> str:
    """Accepts 123-456-7890 or 1234567890 and returns digits only."""
    digits = "".join(ch for ch in str(customer_id) if ch.isdigit())
    if len(digits) != 10:
        raise ToolError(
            f"customer_id must have 10 digits, got '{customer_id}'."
        )
    return digits


def _normalize_date_time(
    value: str, field: str, end_of_day: bool = False
) -> str:
    """Returns 'YYYY-MM-DD HH:MM:SS', the format Campaign uses since v21.

    Accepts YYYY-MM-DD, YYYYMMDD or an already complete datetime. A bare date
    becomes the start of the day, or the end of it for `end_date`, so that a
    campaign ending on a given day runs through that whole day.
    """
    text = str(value).strip()
    date_part, _, time_part = text.partition(" ")
    match = re.fullmatch(r"(\d{4})-?(\d{2})-?(\d{2})", date_part)
    if not match:
        raise ToolError(
            f"{field} must be YYYY-MM-DD, optionally followed by HH:MM:SS "
            f"(got '{value}'). Note that DD/MM/YYYY is not accepted."
        )
    year, month, day = (int(part) for part in match.groups())
    try:
        date(year, month, day)
    except ValueError as exc:
        raise ToolError(f"{field} is not a real date ('{value}'): {exc}.")
    date_part = f"{year:04d}-{month:02d}-{day:02d}"
    if not time_part:
        time_part = "23:59:59" if end_of_day else "00:00:00"
    return f"{date_part} {time_part}"


def _enum(client, enum_name: str, value: str, field: str):
    """Resolves a string onto a Google Ads enum, with a helpful error."""
    enum_type = getattr(client.enums, enum_name)
    try:
        return getattr(enum_type, value.upper())
    except AttributeError:
        valid = [
            name
            for name in dir(enum_type)
            if name.isupper() and name not in ("UNKNOWN", "UNSPECIFIED")
        ]
        raise ToolError(
            f"Invalid {field} '{value}'. Valid values: {', '.join(valid)}."
        )


def _mutate(
    *,
    service_name: str,
    method_name: str,
    request_type: str,
    customer_id: str,
    operations: List[Any],
    confirm: bool,
    summary: str,
) -> Dict[str, Any]:
    """Sends one mutate request, as a dry run unless `confirm` is True."""
    client = utils.get_googleads_client()
    request = client.get_type(request_type)
    request.customer_id = customer_id
    request.operations = operations
    request.validate_only = not confirm
    if hasattr(request, "partial_failure"):
        request.partial_failure = False

    service = utils.get_googleads_service(service_name)

    utils.logger.info(
        "ads_mcp.mutate %s.%s customer=%s validate_only=%s | %s",
        service_name,
        method_name,
        customer_id,
        not confirm,
        summary,
    )

    try:
        response = getattr(service, method_name)(request=request)
    except GoogleAdsException as ex:
        error_msgs = []
        for error in ex.failure.errors:
            location = ""
            if error.location and error.location.field_path_elements:
                path = ".".join(
                    el.field_name
                    for el in error.location.field_path_elements
                    if el.field_name
                )
                location = f" (at {path})"
            error_msgs.append(f"Google Ads API Error: {error.message}{location}")
        raise ToolError(
            f"Request ID: {ex.request_id}\n" + "\n".join(error_msgs)
        )

    if not confirm:
        return {
            "dry_run": True,
            "validated": True,
            "summary": summary,
            "next_step": (
                "The API validated this operation and changed nothing. "
                "Show this plan to the user and call the same tool again with "
                "confirm=True only after they explicitly approve it."
            ),
        }

    return {
        "dry_run": False,
        "committed": True,
        "summary": summary,
        "resource_names": [result.resource_name for result in response.results],
    }


@mutate_mcp.tool(annotations=_WRITE)
def create_campaign_budget(
    customer_id: str,
    name: str,
    amount_micros: int,
    delivery_method: str = "STANDARD",
    explicitly_shared: bool = False,
    confirm: bool = False,
) -> Dict[str, Any]:
    """Creates a campaign budget. Returns its resource name, needed by create_campaign.

    Args:
        customer_id: The id of the customer.
        name: Budget name, unique within the account.
        amount_micros: Daily amount in micros (1 BRL = 1_000_000 micros).
        delivery_method: STANDARD or ACCELERATED.
        explicitly_shared: Whether the budget can be shared across campaigns.
        confirm: False validates only; True commits the change.
    """
    customer_id = _normalize_customer_id(customer_id)
    if amount_micros <= 0:
        raise ToolError("amount_micros must be greater than zero.")

    client = utils.get_googleads_client()
    operation = client.get_type("CampaignBudgetOperation")
    budget = operation.create
    budget.name = name
    budget.amount_micros = amount_micros
    budget.delivery_method = _enum(
        client, "BudgetDeliveryMethodEnum", delivery_method, "delivery_method"
    )
    budget.explicitly_shared = explicitly_shared

    return _mutate(
        service_name="CampaignBudgetService",
        method_name="mutate_campaign_budgets",
        request_type="MutateCampaignBudgetsRequest",
        customer_id=customer_id,
        operations=[operation],
        confirm=confirm,
        summary=(
            f"Create budget '{name}': {amount_micros / 1_000_000:.2f} per day, "
            f"{delivery_method} delivery."
        ),
    )


@mutate_mcp.tool(annotations=_WRITE)
def create_campaign(
    customer_id: str,
    name: str,
    budget_resource_name: str,
    bidding_strategy: str,
    start_date: str,
    end_date: str | None = None,
    advertising_channel_type: str = "SEARCH",
    status: str = "PAUSED",
    target_cpa_micros: int | None = None,
    target_roas: float | None = None,
    cpc_bid_ceiling_micros: int | None = None,
    target_search_network: bool = False,
    target_content_network: bool = False,
    contains_eu_political_advertising: bool = False,
    confirm: bool = False,
) -> Dict[str, Any]:
    """Creates a campaign. Defaults to PAUSED so nothing starts spending by accident.

    Args:
        customer_id: The id of the customer.
        name: Campaign name, unique within the account.
        budget_resource_name: From create_campaign_budget, e.g. customers/123/campaignBudgets/456.
        bidding_strategy: MANUAL_CPC, MAXIMIZE_CONVERSIONS, MAXIMIZE_CONVERSION_VALUE or TARGET_SPEND.
        start_date: YYYY-MM-DD.
        end_date: YYYY-MM-DD, optional.
        advertising_channel_type: SEARCH, DISPLAY, SHOPPING or PERFORMANCE_MAX.
        status: PAUSED or ENABLED.
        target_cpa_micros: Optional target CPA, for MAXIMIZE_CONVERSIONS.
        target_roas: Optional target ROAS, for MAXIMIZE_CONVERSION_VALUE (2.5 = 250%).
        cpc_bid_ceiling_micros: Optional bid ceiling, for TARGET_SPEND.
        target_search_network: Include Google search partners.
        target_content_network: Include the Display network.
        contains_eu_political_advertising: Whether the campaign runs political
            advertising in the EU. Required by the API since v25 -- the request is
            rejected outright when it is absent, so it is always sent. Leave False
            unless the advertiser really does run EU political ads.
        confirm: False validates only; True commits the change.
    """
    customer_id = _normalize_customer_id(customer_id)
    if status.upper() not in ("ENABLED", "PAUSED"):
        raise ToolError("status must be PAUSED or ENABLED when creating.")

    client = utils.get_googleads_client()
    operation = client.get_type("CampaignOperation")
    campaign = operation.create
    campaign.name = name
    campaign.campaign_budget = budget_resource_name
    campaign.status = _enum(client, "CampaignStatusEnum", status, "status")
    campaign.advertising_channel_type = _enum(
        client,
        "AdvertisingChannelTypeEnum",
        advertising_channel_type,
        "advertising_channel_type",
    )
    campaign.start_date_time = _normalize_date_time(start_date, "start_date")
    if end_date:
        campaign.end_date_time = _normalize_date_time(
            end_date, "end_date", end_of_day=True
        )

    strategy = bidding_strategy.upper()
    if strategy == "MANUAL_CPC":
        campaign.manual_cpc.enhanced_cpc_enabled = False
    elif strategy == "MAXIMIZE_CONVERSIONS":
        if target_cpa_micros:
            campaign.maximize_conversions.target_cpa_micros = target_cpa_micros
        else:
            campaign.maximize_conversions = client.get_type(
                "MaximizeConversions"
            )
    elif strategy == "MAXIMIZE_CONVERSION_VALUE":
        if target_roas:
            campaign.maximize_conversion_value.target_roas = target_roas
        else:
            campaign.maximize_conversion_value = client.get_type(
                "MaximizeConversionValue"
            )
    elif strategy == "TARGET_SPEND":
        if cpc_bid_ceiling_micros:
            campaign.target_spend.cpc_bid_ceiling_micros = (
                cpc_bid_ceiling_micros
            )
        else:
            campaign.target_spend = client.get_type("TargetSpend")
    else:
        raise ToolError(
            "bidding_strategy must be MANUAL_CPC, MAXIMIZE_CONVERSIONS, "
            "MAXIMIZE_CONVERSION_VALUE or TARGET_SPEND."
        )

    campaign.network_settings.target_google_search = True
    campaign.network_settings.target_search_network = target_search_network
    campaign.network_settings.target_content_network = target_content_network
    campaign.network_settings.target_partner_search_network = False

    # Obrigatorio desde a v25: sem este campo a API recusa o create inteiro com
    # "The required field was not present". Nao ha default do lado do Google.
    campaign.contains_eu_political_advertising = _enum(
        client,
        "EuPoliticalAdvertisingStatusEnum",
        (
            "CONTAINS_EU_POLITICAL_ADVERTISING"
            if contains_eu_political_advertising
            else "DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING"
        ),
        "contains_eu_political_advertising",
    )

    window = f"from {start_date}" + (f" to {end_date}" if end_date else "")
    return _mutate(
        service_name="CampaignService",
        method_name="mutate_campaigns",
        request_type="MutateCampaignsRequest",
        customer_id=customer_id,
        operations=[operation],
        confirm=confirm,
        summary=(
            f"Create {advertising_channel_type} campaign '{name}' ({status}), "
            f"bidding {strategy}, {window}."
        ),
    )


@mutate_mcp.tool(annotations=_WRITE)
def add_campaign_geo_targets(
    customer_id: str,
    campaign_resource_name: str,
    geo_target_constant_ids: List[str],
    negative: bool = False,
    confirm: bool = False,
) -> Dict[str, Any]:
    """Adds location targeting to a campaign.

    Args:
        customer_id: The id of the customer.
        campaign_resource_name: e.g. customers/123/campaigns/456.
        geo_target_constant_ids: Numeric ids, e.g. ['2076'] for Brazil, ['1001541'] for Sao Paulo.
        negative: True excludes the locations instead of targeting them.
        confirm: False validates only; True commits the change.
    """
    customer_id = _normalize_customer_id(customer_id)
    if not geo_target_constant_ids:
        raise ToolError("geo_target_constant_ids must not be empty.")

    client = utils.get_googleads_client()
    operations = []
    for geo_id in geo_target_constant_ids:
        operation = client.get_type("CampaignCriterionOperation")
        criterion = operation.create
        criterion.campaign = campaign_resource_name
        criterion.negative = negative
        criterion.location.geo_target_constant = (
            f"geoTargetConstants/{str(geo_id).strip()}"
        )
        operations.append(operation)

    verb = "Exclude" if negative else "Target"
    return _mutate(
        service_name="CampaignCriterionService",
        method_name="mutate_campaign_criteria",
        request_type="MutateCampaignCriteriaRequest",
        customer_id=customer_id,
        operations=operations,
        confirm=confirm,
        summary=(
            f"{verb} {len(operations)} location(s) "
            f"({', '.join(str(g) for g in geo_target_constant_ids)}) on the campaign."
        ),
    )


@mutate_mcp.tool(annotations=_WRITE)
def add_campaign_languages(
    customer_id: str,
    campaign_resource_name: str,
    language_constant_ids: List[str],
    confirm: bool = False,
) -> Dict[str, Any]:
    """Adds language targeting to a campaign.

    Args:
        customer_id: The id of the customer.
        campaign_resource_name: e.g. customers/123/campaigns/456.
        language_constant_ids: Numeric ids, e.g. ['1014'] for Portuguese, ['1000'] for English.
        confirm: False validates only; True commits the change.
    """
    customer_id = _normalize_customer_id(customer_id)
    if not language_constant_ids:
        raise ToolError("language_constant_ids must not be empty.")

    client = utils.get_googleads_client()
    operations = []
    for language_id in language_constant_ids:
        operation = client.get_type("CampaignCriterionOperation")
        criterion = operation.create
        criterion.campaign = campaign_resource_name
        criterion.language.language_constant = (
            f"languageConstants/{str(language_id).strip()}"
        )
        operations.append(operation)

    return _mutate(
        service_name="CampaignCriterionService",
        method_name="mutate_campaign_criteria",
        request_type="MutateCampaignCriteriaRequest",
        customer_id=customer_id,
        operations=operations,
        confirm=confirm,
        summary=(
            f"Target {len(operations)} language(s) "
            f"({', '.join(str(l) for l in language_constant_ids)}) on the campaign."
        ),
    )


@mutate_mcp.tool(annotations=_WRITE)
def add_campaign_negative_keywords(
    customer_id: str,
    campaign_resource_name: str,
    keywords: List[Dict[str, str]],
    confirm: bool = False,
) -> Dict[str, Any]:
    """Adds campaign-level negative keywords.

    Args:
        customer_id: The id of the customer.
        campaign_resource_name: e.g. customers/123/campaigns/456.
        keywords: [{'text': 'gratis', 'match_type': 'BROAD'}, ...]. match_type
            is EXACT, PHRASE or BROAD and defaults to BROAD.
        confirm: False validates only; True commits the change.
    """
    customer_id = _normalize_customer_id(customer_id)
    if not keywords:
        raise ToolError("keywords must not be empty.")

    client = utils.get_googleads_client()
    operations = []
    for item in keywords:
        text = (item.get("text") or "").strip()
        if not text:
            raise ToolError("Every keyword needs a non-empty 'text'.")
        operation = client.get_type("CampaignCriterionOperation")
        criterion = operation.create
        criterion.campaign = campaign_resource_name
        criterion.negative = True
        criterion.keyword.text = text
        criterion.keyword.match_type = _enum(
            client,
            "KeywordMatchTypeEnum",
            item.get("match_type", "BROAD"),
            "match_type",
        )
        operations.append(operation)

    return _mutate(
        service_name="CampaignCriterionService",
        method_name="mutate_campaign_criteria",
        request_type="MutateCampaignCriteriaRequest",
        customer_id=customer_id,
        operations=operations,
        confirm=confirm,
        summary=f"Add {len(operations)} campaign-level negative keyword(s).",
    )


@mutate_mcp.tool(annotations=_WRITE)
def create_ad_group(
    customer_id: str,
    campaign_resource_name: str,
    name: str,
    cpc_bid_micros: int | None = None,
    status: str = "ENABLED",
    confirm: bool = False,
) -> Dict[str, Any]:
    """Creates an ad group inside a campaign.

    Args:
        customer_id: The id of the customer.
        campaign_resource_name: e.g. customers/123/campaigns/456.
        name: Ad group name, unique within the campaign.
        cpc_bid_micros: Default CPC bid in micros; only used with manual bidding.
        status: ENABLED or PAUSED.
        confirm: False validates only; True commits the change.
    """
    customer_id = _normalize_customer_id(customer_id)

    client = utils.get_googleads_client()
    operation = client.get_type("AdGroupOperation")
    ad_group = operation.create
    ad_group.name = name
    ad_group.campaign = campaign_resource_name
    ad_group.status = _enum(client, "AdGroupStatusEnum", status, "status")
    ad_group.type_ = _enum(
        client, "AdGroupTypeEnum", "SEARCH_STANDARD", "ad_group_type"
    )
    if cpc_bid_micros:
        ad_group.cpc_bid_micros = cpc_bid_micros

    return _mutate(
        service_name="AdGroupService",
        method_name="mutate_ad_groups",
        request_type="MutateAdGroupsRequest",
        customer_id=customer_id,
        operations=[operation],
        confirm=confirm,
        summary=f"Create ad group '{name}' ({status}).",
    )


@mutate_mcp.tool(annotations=_WRITE)
def add_keywords(
    customer_id: str,
    ad_group_resource_name: str,
    keywords: List[Dict[str, Any]],
    status: str = "ENABLED",
    confirm: bool = False,
) -> Dict[str, Any]:
    """Adds positive keywords to an ad group.

    Args:
        customer_id: The id of the customer.
        ad_group_resource_name: e.g. customers/123/adGroups/456.
        keywords: [{'text': 'loja virtual', 'match_type': 'PHRASE', 'cpc_bid_micros': 2000000}, ...].
            match_type is EXACT, PHRASE or BROAD; cpc_bid_micros is optional.
        status: ENABLED or PAUSED for the new keywords.
        confirm: False validates only; True commits the change.
    """
    customer_id = _normalize_customer_id(customer_id)
    if not keywords:
        raise ToolError("keywords must not be empty.")

    client = utils.get_googleads_client()
    operations = []
    for item in keywords:
        text = (item.get("text") or "").strip()
        if not text:
            raise ToolError("Every keyword needs a non-empty 'text'.")
        operation = client.get_type("AdGroupCriterionOperation")
        criterion = operation.create
        criterion.ad_group = ad_group_resource_name
        criterion.status = _enum(
            client, "AdGroupCriterionStatusEnum", status, "status"
        )
        criterion.keyword.text = text
        criterion.keyword.match_type = _enum(
            client,
            "KeywordMatchTypeEnum",
            item.get("match_type", "PHRASE"),
            "match_type",
        )
        if item.get("cpc_bid_micros"):
            criterion.cpc_bid_micros = int(item["cpc_bid_micros"])
        operations.append(operation)

    preview = ", ".join(
        f"[{k.get('match_type', 'PHRASE')}] {k['text']}" for k in keywords[:5]
    )
    if len(keywords) > 5:
        preview += f", +{len(keywords) - 5} more"

    return _mutate(
        service_name="AdGroupCriterionService",
        method_name="mutate_ad_group_criteria",
        request_type="MutateAdGroupCriteriaRequest",
        customer_id=customer_id,
        operations=operations,
        confirm=confirm,
        summary=f"Add {len(operations)} keyword(s): {preview}.",
    )


@mutate_mcp.tool(annotations=_WRITE)
def create_responsive_search_ad(
    customer_id: str,
    ad_group_resource_name: str,
    final_url: str,
    headlines: List[str],
    descriptions: List[str],
    path1: str | None = None,
    path2: str | None = None,
    status: str = "ENABLED",
    confirm: bool = False,
) -> Dict[str, Any]:
    """Creates a Responsive Search Ad in an ad group.

    Character limits are enforced locally before the request, so a bad asset
    fails immediately instead of costing an API round trip.

    Args:
        customer_id: The id of the customer.
        ad_group_resource_name: e.g. customers/123/adGroups/456.
        final_url: Landing page URL.
        headlines: 3 to 15 headlines, max 30 characters each.
        descriptions: 2 to 4 descriptions, max 90 characters each.
        path1: Optional display path segment, max 15 characters.
        path2: Optional second display path segment, max 15 characters.
        status: ENABLED or PAUSED.
        confirm: False validates only; True commits the change.
    """
    customer_id = _normalize_customer_id(customer_id)

    if not 3 <= len(headlines) <= 15:
        raise ToolError(
            f"A responsive search ad needs 3 to 15 headlines, got {len(headlines)}."
        )
    if not 2 <= len(descriptions) <= 4:
        raise ToolError(
            f"A responsive search ad needs 2 to 4 descriptions, got {len(descriptions)}."
        )
    for headline in headlines:
        if len(headline) > _RSA_HEADLINE_MAX:
            raise ToolError(
                f"Headline over {_RSA_HEADLINE_MAX} characters "
                f"({len(headline)}): '{headline}'."
            )
    for description in descriptions:
        if len(description) > _RSA_DESCRIPTION_MAX:
            raise ToolError(
                f"Description over {_RSA_DESCRIPTION_MAX} characters "
                f"({len(description)}): '{description}'."
            )
    for label, path in (("path1", path1), ("path2", path2)):
        if path and len(path) > 15:
            raise ToolError(f"{label} is over 15 characters: '{path}'.")

    client = utils.get_googleads_client()
    operation = client.get_type("AdGroupAdOperation")
    ad_group_ad = operation.create
    ad_group_ad.ad_group = ad_group_resource_name
    ad_group_ad.status = _enum(
        client, "AdGroupAdStatusEnum", status, "status"
    )
    ad_group_ad.ad.final_urls.append(final_url)

    for headline in headlines:
        asset = client.get_type("AdTextAsset")
        asset.text = headline
        ad_group_ad.ad.responsive_search_ad.headlines.append(asset)
    for description in descriptions:
        asset = client.get_type("AdTextAsset")
        asset.text = description
        ad_group_ad.ad.responsive_search_ad.descriptions.append(asset)
    if path1:
        ad_group_ad.ad.responsive_search_ad.path1 = path1
    if path2:
        ad_group_ad.ad.responsive_search_ad.path2 = path2

    return _mutate(
        service_name="AdGroupAdService",
        method_name="mutate_ad_group_ads",
        request_type="MutateAdGroupAdsRequest",
        customer_id=customer_id,
        operations=[operation],
        confirm=confirm,
        summary=(
            f"Create RSA in the ad group ({status}) pointing to {final_url}, "
            f"with {len(headlines)} headlines and {len(descriptions)} descriptions."
        ),
    )


@mutate_mcp.tool(annotations=_DESTRUCTIVE)
def update_campaign_status(
    customer_id: str,
    campaign_resource_name: str,
    status: str,
    confirm: bool = False,
) -> Dict[str, Any]:
    """Enables, pauses or removes a campaign.

    Args:
        customer_id: The id of the customer.
        campaign_resource_name: e.g. customers/123/campaigns/456.
        status: ENABLED, PAUSED or REMOVED.
        confirm: False validates only; True commits the change.
    """
    customer_id = _normalize_customer_id(customer_id)
    if status.upper() not in _ENTITY_STATUSES:
        raise ToolError("status must be ENABLED, PAUSED or REMOVED.")

    client = utils.get_googleads_client()
    operation = client.get_type("CampaignOperation")
    campaign = operation.update
    campaign.resource_name = campaign_resource_name
    campaign.status = _enum(client, "CampaignStatusEnum", status, "status")
    client.copy_from(
        operation.update_mask,
        protobuf_helpers.field_mask(None, campaign._pb),
    )

    return _mutate(
        service_name="CampaignService",
        method_name="mutate_campaigns",
        request_type="MutateCampaignsRequest",
        customer_id=customer_id,
        operations=[operation],
        confirm=confirm,
        summary=f"Set campaign {campaign_resource_name} to {status.upper()}.",
    )


@mutate_mcp.tool(annotations=_WRITE)
def update_campaign_budget_amount(
    customer_id: str,
    budget_resource_name: str,
    amount_micros: int,
    confirm: bool = False,
) -> Dict[str, Any]:
    """Changes the daily amount of an existing campaign budget.

    Args:
        customer_id: The id of the customer.
        budget_resource_name: e.g. customers/123/campaignBudgets/456.
        amount_micros: New daily amount in micros (1 BRL = 1_000_000 micros).
        confirm: False validates only; True commits the change.
    """
    customer_id = _normalize_customer_id(customer_id)
    if amount_micros <= 0:
        raise ToolError("amount_micros must be greater than zero.")

    client = utils.get_googleads_client()
    operation = client.get_type("CampaignBudgetOperation")
    budget = operation.update
    budget.resource_name = budget_resource_name
    budget.amount_micros = amount_micros
    client.copy_from(
        operation.update_mask,
        protobuf_helpers.field_mask(None, budget._pb),
    )

    return _mutate(
        service_name="CampaignBudgetService",
        method_name="mutate_campaign_budgets",
        request_type="MutateCampaignBudgetsRequest",
        customer_id=customer_id,
        operations=[operation],
        confirm=confirm,
        summary=(
            f"Set budget {budget_resource_name} to "
            f"{amount_micros / 1_000_000:.2f} per day."
        ),
    )


@mutate_mcp.tool(annotations=_DESTRUCTIVE)
def update_ad_group_status(
    customer_id: str,
    ad_group_resource_name: str,
    status: str,
    confirm: bool = False,
) -> Dict[str, Any]:
    """Enables, pauses or removes an ad group.

    Args:
        customer_id: The id of the customer.
        ad_group_resource_name: e.g. customers/123/adGroups/456.
        status: ENABLED, PAUSED or REMOVED.
        confirm: False validates only; True commits the change.
    """
    customer_id = _normalize_customer_id(customer_id)
    if status.upper() not in _ENTITY_STATUSES:
        raise ToolError("status must be ENABLED, PAUSED or REMOVED.")

    client = utils.get_googleads_client()
    operation = client.get_type("AdGroupOperation")
    ad_group = operation.update
    ad_group.resource_name = ad_group_resource_name
    ad_group.status = _enum(client, "AdGroupStatusEnum", status, "status")
    client.copy_from(
        operation.update_mask,
        protobuf_helpers.field_mask(None, ad_group._pb),
    )

    return _mutate(
        service_name="AdGroupService",
        method_name="mutate_ad_groups",
        request_type="MutateAdGroupsRequest",
        customer_id=customer_id,
        operations=[operation],
        confirm=confirm,
        summary=f"Set ad group {ad_group_resource_name} to {status.upper()}.",
    )


@mutate_mcp.tool(annotations=_DESTRUCTIVE)
def update_keyword_status(
    customer_id: str,
    criterion_resource_names: List[str],
    status: str,
    confirm: bool = False,
) -> Dict[str, Any]:
    """Enables, pauses or removes existing keywords, in bulk.

    This is the day-to-day optimisation move: a search term report shows a
    keyword burning budget on the wrong intent, and it has to go without
    touching the rest of the ad group.

    Get the resource names from a GAQL query on `ad_group_criterion`, e.g.
    SELECT ad_group_criterion.resource_name, ad_group_criterion.keyword.text
    FROM ad_group_criterion WHERE ad_group_criterion.type = 'KEYWORD'.

    Args:
        customer_id: The id of the customer.
        criterion_resource_names: e.g. ['customers/123/adGroupCriteria/456~789'].
            The '~' separates the ad group id from the criterion id.
        status: ENABLED, PAUSED or REMOVED. REMOVED cannot be undone — the
            keyword's history stays queryable, but it cannot be re-enabled.
        confirm: False validates only; True commits the change.
    """
    customer_id = _normalize_customer_id(customer_id)
    if status.upper() not in _ENTITY_STATUSES:
        raise ToolError("status must be ENABLED, PAUSED or REMOVED.")
    if not criterion_resource_names:
        raise ToolError("criterion_resource_names must not be empty.")

    # Catch an ad group resource name passed by mistake: without the '~' the
    # API would reject it anyway, but late and with a much worse message.
    for name in criterion_resource_names:
        if "adGroupCriteria/" not in name or "~" not in name:
            raise ToolError(
                f"'{name}' is not an ad group criterion resource name. Expected "
                "customers/<cid>/adGroupCriteria/<ad_group_id>~<criterion_id>."
            )

    client = utils.get_googleads_client()
    operations = []
    for name in criterion_resource_names:
        operation = client.get_type("AdGroupCriterionOperation")
        criterion = operation.update
        criterion.resource_name = name
        criterion.status = _enum(
            client, "AdGroupCriterionStatusEnum", status, "status"
        )
        client.copy_from(
            operation.update_mask,
            protobuf_helpers.field_mask(None, criterion._pb),
        )
        operations.append(operation)

    preview = ", ".join(criterion_resource_names[:3])
    if len(criterion_resource_names) > 3:
        preview += f", +{len(criterion_resource_names) - 3} more"

    return _mutate(
        service_name="AdGroupCriterionService",
        method_name="mutate_ad_group_criteria",
        request_type="MutateAdGroupCriteriaRequest",
        customer_id=customer_id,
        operations=operations,
        confirm=confirm,
        summary=(
            f"Set {len(operations)} keyword(s) to {status.upper()}: {preview}."
        ),
    )
