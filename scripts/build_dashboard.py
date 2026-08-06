from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qs, urlparse

import requests

ROOT = Path(__file__).resolve().parents[1]
OUT_PATH = ROOT / "docs" / "data" / "dashboard.json"
REP_TAGS_PATH = ROOT / "config" / "rep_tags.json"
SPECIAL_CUSTOMERS_PATH = ROOT / "config" / "special_customers.json"

API_VERSION = os.getenv("SHOPIFY_API_VERSION", "2024-01")
STORE = os.environ.get("SHOPIFY_STORE", "").strip()
TOKEN = os.environ.get("SHOPIFY_TOKEN", "").strip()

RATE_NEW = 0.12
RATE_RECURRING = 0.08
RATE_SPECIAL = 0.01

EXCLUDE_ORDER_TAGS = {
    "subscription recurring order",
    "subscription order",
}
COMMISSION_ELIGIBLE_ORDER_TAG = "commissioneligible"
EXCLUDE_PRODUCT_TAGS = {
    "drop ship",
    "drop_ship",
    "shopify collective",
    "dropship",
    "autoship",
}

# IMPORTANT: subscriber/customer tags are intentionally NOT exclusion criteria.
# A subscriber's one-time purchase should still earn commission.
# Special 1% customers are normal, non-secret business rules stored in
# config/special_customers.json. Shopify credentials remain the only secrets.

LOOKBACK_DAYS = int(os.getenv("LOOKBACK_DAYS", "190"))
REQUEST_TIMEOUT = 45


class BuildError(RuntimeError):
    pass


def normalize_tag(value: Any) -> str:
    return str(value or "").strip().lower()


def split_tags(value: Any) -> list[str]:
    return [normalize_tag(x) for x in str(value or "").split(",") if normalize_tag(x)]


def first_matching_tag(tags: Any, candidates: set[str]) -> str | None:
    tag_set = set(split_tags(tags))
    for candidate in candidates:
        if candidate in tag_set:
            return candidate
    return None


def load_special_customers() -> tuple[set[str], set[str]]:
    try:
        payload = json.loads(SPECIAL_CUSTOMERS_PATH.read_text(encoding="utf-8"))
        values = payload.get("special_customers", [])
    except (OSError, json.JSONDecodeError) as exc:
        raise BuildError(f"Cannot read {SPECIAL_CUSTOMERS_PATH}: {exc}") from exc

    ids: set[str] = set()
    emails: set[str] = set()
    for row in values:
        if not isinstance(row, dict) or row.get("enabled", True) is False:
            continue
        customer_id = str(row.get("shopify_customer_id") or "").strip()
        email = str(row.get("email") or "").strip().lower()
        if customer_id:
            ids.add(customer_id)
        if email:
            emails.add(email)
    return ids, emails


def load_rep_tags() -> list[str]:
    try:
        payload = json.loads(REP_TAGS_PATH.read_text(encoding="utf-8"))
        values = payload.get("rep_tags", [])
    except (OSError, json.JSONDecodeError) as exc:
        raise BuildError(f"Cannot read {REP_TAGS_PATH}: {exc}") from exc

    tags: list[str] = []
    seen: set[str] = set()
    for raw in values:
        tag = str(raw or "").strip().upper()
        if tag and tag not in seen:
            tags.append(tag)
            seen.add(tag)
    if not tags:
        raise BuildError("config/rep_tags.json has no active rep tags")
    return tags


def request_json(url: str, params: dict[str, Any] | None = None) -> tuple[dict[str, Any], requests.Response]:
    headers = {"X-Shopify-Access-Token": TOKEN, "Accept": "application/json"}
    for attempt in range(5):
        response = requests.get(url, headers=headers, params=params, timeout=REQUEST_TIMEOUT)
        if response.status_code == 429:
            wait = float(response.headers.get("Retry-After", "2")) + attempt
            time.sleep(wait)
            continue
        if 500 <= response.status_code < 600 and attempt < 4:
            time.sleep(2 ** attempt)
            continue
        if response.status_code != 200:
            raise BuildError(f"Shopify API returned HTTP {response.status_code}: {response.text[:500]}")
        return response.json(), response
    raise BuildError("Shopify API rate limit retry limit exceeded")


def fetch_orders(start_iso: str, end_iso: str) -> list[dict[str, Any]]:
    base = f"https://{STORE}/admin/api/{API_VERSION}/orders.json"
    params: dict[str, Any] | None = {
        "status": "any",
        "created_at_min": start_iso,
        "created_at_max": end_iso,
        "limit": 250,
        "fields": "id,name,email,tags,created_at,line_items,customer,note_attributes,source_name",
    }
    url = base
    orders: list[dict[str, Any]] = []

    while url:
        payload, response = request_json(url, params=params)
        orders.extend(payload.get("orders", []))
        params = None
        url = next_link(response.headers.get("Link", ""))
    return orders


def next_link(link_header: str) -> str | None:
    for part in link_header.split(","):
        sections = part.split(";")
        if len(sections) < 2:
            continue
        url_part = sections[0].strip()
        rel_part = ";".join(sections[1:])
        if 'rel="next"' in rel_part and url_part.startswith("<") and url_part.endswith(">"):
            return url_part[1:-1]
    return None


def fetch_product_tags(product_ids: Iterable[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for index, product_id in enumerate(sorted(set(product_ids))):
        url = f"https://{STORE}/admin/api/{API_VERSION}/products/{product_id}.json"
        try:
            payload, _ = request_json(url, params={"fields": "id,tags"})
            result[product_id] = payload.get("product", {}).get("tags", "") or ""
        except BuildError as exc:
            print(f"Warning: product {product_id} tags unavailable: {exc}", file=sys.stderr)
            result[product_id] = ""
        if index and index % 25 == 0:
            time.sleep(1)
    return result


def source_has_concierge_context(tag_list: list[str]) -> bool:
    return "concierge" in tag_list or COMMISSION_ELIGIBLE_ORDER_TAG in tag_list


def matching_rep_tags(customer_tags: Any, order_tags: Any, valid_rep_tags: list[str]) -> dict[str, list[str]]:
    valid = {tag.upper() for tag in valid_rep_tags}
    customer_list = split_tags(customer_tags)
    order_list = split_tags(order_tags)
    customer_context = source_has_concierge_context(customer_list)
    order_context = source_has_concierge_context(order_list)

    customer = []
    order = []
    ignored_customer = []
    ignored_order = []

    for tag in customer_list:
        upper = tag.upper()
        if upper not in valid:
            continue
        (customer if customer_context else ignored_customer).append(upper)

    for tag in order_list:
        upper = tag.upper()
        if upper not in valid:
            continue
        (order if order_context else ignored_order).append(upper)

    customer = list(dict.fromkeys(customer))
    order = list(dict.fromkeys(order))
    ignored_customer = list(dict.fromkeys(ignored_customer))
    ignored_order = list(dict.fromkeys(ignored_order))
    all_tags = list(dict.fromkeys(customer + order))
    ignored_all = list(dict.fromkeys(ignored_customer + ignored_order))
    return {
        "customer": customer,
        "order": order,
        "all": all_tags,
        "ignored_customer": ignored_customer,
        "ignored_order": ignored_order,
        "ignored_all": ignored_all,
    }


def extract_rep_tag(found: dict[str, list[str]]) -> str | None:
    if found["customer"]:
        return found["customer"][0]
    if found["order"]:
        return found["order"][0]
    return None


def has_cj_affiliate(note_attributes: list[dict[str, Any]] | None) -> bool:
    for attr in note_attributes or []:
        key = str(attr.get("name") or attr.get("key") or "").lower()
        if "cjevent" in key:
            return True
    return False


def line_has_selling_plan(item: dict[str, Any]) -> bool:
    allocation = item.get("selling_plan_allocation")
    if allocation:
        return True

    # Some integrations expose an exact selling-plan/subscription marker as a line property.
    # We only accept exact keys to avoid treating ordinary product text as a subscription.
    exact_property_names = {
        "selling_plan_id",
        "selling plan id",
        "subscription_id",
        "subscription id",
        "smartrr_subscription_id",
    }
    for prop in item.get("properties") or []:
        name = normalize_tag(prop.get("name"))
        value = str(prop.get("value") or "").strip()
        if name in exact_property_names and value:
            return True
    return False


def actual_subscription_reason(order: dict[str, Any], item: dict[str, Any]) -> str | None:
    order_tag = first_matching_tag(order.get("tags", ""), EXCLUDE_ORDER_TAGS)
    if order_tag:
        return f"{order_tag} [Order Tag]"
    if line_has_selling_plan(item):
        return "Selling Plan / Subscription [Line Item]"
    return None


def is_special_customer(order: dict[str, Any], special_ids: set[str], special_emails: set[str]) -> bool:
    customer = order.get("customer") or {}
    customer_id = str(customer.get("id") or "").strip()
    email = str(customer.get("email") or order.get("email") or "").strip().lower()
    return bool((customer_id and customer_id in special_ids) or (email and email in special_emails))


def customer_name(order: dict[str, Any]) -> str:
    customer = order.get("customer") or {}
    full = f"{customer.get('first_name') or ''} {customer.get('last_name') or ''}".strip()
    return full or "N/A"


def money(value: float) -> float:
    return round(value + 1e-9, 2)


def build_report(orders: list[dict[str, Any]], valid_rep_tags: list[str], product_tags: dict[str, str], special_ids: set[str], special_emails: set[str]) -> dict[str, Any]:
    rep_map: dict[str, dict[str, Any]] = {}

    for order in orders:
        customer = order.get("customer") or {}
        customer_tags = customer.get("tags", "") or ""
        order_tags = order.get("tags", "") or ""
        found = matching_rep_tags(customer_tags, order_tags, valid_rep_tags)
        rep_tag = extract_rep_tag(found)
        if not rep_tag:
            continue

        is_recurring = int(customer.get("orders_count") or 0) > 1
        special = is_special_customer(order, special_ids, special_emails)
        cj_reason = "CJ Affiliate [Order Attribute]" if has_cj_affiliate(order.get("note_attributes")) else None
        eligible_order = COMMISSION_ELIGIBLE_ORDER_TAG in split_tags(order_tags)
        rep_data = rep_map.setdefault(rep_tag, {"rep": rep_tag, "orders": []})

        for item in order.get("line_items") or []:
            if item.get("title") == "Shipping":
                continue

            quantity = int(item.get("quantity") or 0)
            gross = float(item.get("price") or 0) * quantity
            discounts = sum(float(x.get("amount") or 0) for x in item.get("discount_allocations") or [])
            net = gross - discounts
            product_id = str(item.get("product_id") or "")
            product_reason = first_matching_tag(product_tags.get(product_id, ""), EXCLUDE_PRODUCT_TAGS)
            subscription_reason = actual_subscription_reason(order, item)

            exclusion_reason = None
            eligible_reason = None
            if subscription_reason:
                exclusion_reason = subscription_reason
            elif cj_reason:
                exclusion_reason = cj_reason
            elif product_reason and not eligible_order:
                exclusion_reason = f"{product_reason} [Product Tag]"
            elif product_reason and eligible_order:
                eligible_reason = f"{COMMISSION_ELIGIBLE_ORDER_TAG} [Order Tag] · overrides {product_reason} [Product Tag]"
            elif eligible_order:
                eligible_reason = f"{COMMISSION_ELIGIBLE_ORDER_TAG} [Order Tag]"

            if net <= 0:
                order_type, rate = "Return", 0.0
            elif exclusion_reason:
                order_type, rate = "Excluded", 0.0
            elif special:
                order_type, rate = "Special 1%", RATE_SPECIAL
            elif is_recurring:
                order_type, rate = "Recurring", RATE_RECURRING
            else:
                order_type, rate = "New", RATE_NEW

            rep_data["orders"].append({
                "customer": customer_name(order),
                "customer_id": str(customer.get("id") or ""),
                "product": item.get("title") or "",
                "order_id": order.get("name") or "",
                "order_date": order.get("created_at") or "",
                "net": money(net),
                "gross": money(gross),
                "type": order_type,
                "rate": rate,
                "commission": money(net * rate),
                "exclusion_reason": exclusion_reason,
                "eligible_reason": eligible_reason,
                "order_tags": order_tags,
                "rep_tags": ", ".join(found["all"]),
                "rep_tag_count": len(found["all"]),
                "customer_rep_tags": ", ".join(found["customer"]),
                "order_rep_tags": ", ".join(found["order"]),
                "rep_tag_warning": len(found["all"]) > 1,
                "ignored_rep_tags": ", ".join(found["ignored_all"]),
                "special_customer": special,
                "subscription_detected": bool(subscription_reason),
            })

    result = []
    for rep in sorted(rep_map):
        data = rep_map[rep]
        gross_sales = net_sales = new_sales = rec_sales = special_sales = excl_sales = total_comm = 0.0
        duo_combos: set[str] = set()
        duo_count = 0
        for row in data["orders"]:
            gross_sales += row["gross"]
            if row["rep_tag_warning"]:
                duo_count += 1
                duo_combos.add(row["rep_tags"])
            if row["type"] in {"Excluded", "Return"}:
                excl_sales += row["net"]
            else:
                net_sales += row["net"]
                total_comm += row["commission"]
                if row["type"] == "New":
                    new_sales += row["net"]
                elif row["type"] == "Recurring":
                    rec_sales += row["net"]
                elif row["type"] == "Special 1%":
                    special_sales += row["net"]

        result.append({
            "rep": rep,
            "gross_sales": money(gross_sales),
            "net_sales": money(net_sales),
            "new_sales": money(new_sales),
            "rec_sales": money(rec_sales),
            "special_sales": money(special_sales),
            "excl_sales": money(excl_sales),
            "commission": money(total_comm),
            "duo_tag_count": duo_count,
            "duo_tag_combos": sorted(duo_combos),
            "orders": data["orders"],
        })

    return {"reps": result}


def main() -> int:
    if not STORE or not TOKEN:
        raise BuildError("SHOPIFY_STORE and SHOPIFY_TOKEN must be configured as GitHub Actions secrets")

    now = datetime.now(timezone.utc)
    start = now - timedelta(days=LOOKBACK_DAYS)
    start_iso = start.strftime("%Y-%m-%dT%H:%M:%SZ")
    end_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    rep_tags = load_rep_tags()
    special_ids, special_emails = load_special_customers()
    orders = fetch_orders(start_iso, end_iso)

    product_ids = {
        str(item.get("product_id"))
        for order in orders
        for item in order.get("line_items") or []
        if item.get("product_id")
    }
    product_tags = fetch_product_tags(product_ids)
    report = build_report(orders, rep_tags, product_tags, special_ids, special_emails)
    payload = {
        "ok": True,
        "generated": now.strftime("%b %-d, %Y %H:%M UTC"),
        "generated_iso": now.isoformat(),
        "lookback_start": start_iso,
        "lookback_end": end_iso,
        "valid_rep_tags": rep_tags,
        "special_customer_rule_enabled": bool(special_ids or special_emails),
        "subscription_logic": "Exclude only actual subscription orders detected by exact order tags or line-item selling plan markers; customer subscription tags alone are not excluded.",
        **report,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {OUT_PATH} with {len(orders)} Shopify orders and {len(report['reps'])} reps")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BuildError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
