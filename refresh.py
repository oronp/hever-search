"""Build a searchable catalog from the public Hever merchant index."""

import argparse
import json
import re
import time
from ast import literal_eval
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode, urlparse
from urllib.request import HTTPCookieProcessor, build_opener

from bs4 import BeautifulSoup

INDEX_URL = "https://www.mcc.co.il/st_reshet_public.aspx"
OFFER_URL = "https://www.mcc.co.il/site/pg/st_reshet_out&p1="
SUBCATEGORIES = re.compile(r"\bvar\s+new_types\s*=\s*new Array\s*\((.*?)\[\s*\]\s*\);", re.DOTALL)
SUBCATEGORY = re.compile(r"new Array\(\s*(\d+)\s*,\s*(\d+)\s*,\s*('(?:[^'\\]|\\.)*')\s*\)")
MERCHANT_CLICK = re.compile(r"\s*mclick\(\s*['\"]?(\d+)['\"]?\s*\)\s*;?\s*")
NO_RESULTS = "אין תוצאות המתאימות לחיפוש"
WORD = re.compile(r"[א-תA-Za-z]{3,}")
STOP = set("אשר אלא אלו האתר הינה הינו החל הזה החברה הכול הכל ועוד הוא היא ויש כמו ללא להם לכל לכם לנו כאן ניתן עוד עבור אחד אנחנו ביותר באתר מגוון מוצרים מציעה כולל כוללנית החברה מהווה בעולם בישראל הלקוחות שלנו השירות".split())


def clean(text):
    return " ".join(text.split())


def fetch(client, url, fields=None):
    payload = urlencode(fields).encode() if fields is not None else None
    for attempt in range(2):
        try:
            with client.open(url, data=payload, timeout=25) as response:
                destination = urlparse(response.url)
                if destination.scheme != "https" or destination.hostname != "www.mcc.co.il":
                    raise ValueError(f"Unexpected redirect from {url}")
                return response.read()
        except HTTPError as error:
            error.close()
            raise
        except OSError as error:
            if attempt:
                raise
            print(f"Connection interrupted. Retrying once after 2s: {error}", flush=True)
            time.sleep(2)


def get_index(client):
    for attempt in range(2):
        try:
            return fetch(client, INDEX_URL)
        except HTTPError as error:
            if error.code != 429 or attempt:
                raise RuntimeError(f"Public index unavailable: HTTP {error.code}") from error
            retry_after = error.headers.get("Retry-After", "60")
            try:
                delay = int(retry_after) if retry_after.isdecimal() else (
                    parsedate_to_datetime(retry_after) - datetime.now(timezone.utc)
                ).total_seconds()
            except (TypeError, ValueError, OverflowError):
                delay = 60
            if delay > 120:
                raise RuntimeError(f"Public index returned HTTP 429 and requested a wait of {delay:.0f}s") from error
            delay = max(1, delay)
            print(f"Catalog returned HTTP 429. Retrying once after {delay:.0f}s.", flush=True)
            time.sleep(delay)


def parse_categories(page):
    categories = {
        option["value"]: (option.get_text(strip=True), [])
        for option in page.select("#Select1 option[value]:not([disabled])")
        if option["value"] not in ("", "0")
    }
    if not categories:
        raise RuntimeError("No categories found on the public index")
    script = page.find("script", string=SUBCATEGORIES)
    if script is None:
        raise RuntimeError("Could not find subcategories on the public index")
    block = SUBCATEGORIES.search(script.string)[1]
    if SUBCATEGORY.sub("", block).strip(", \t\r\n"):
        raise RuntimeError("Unexpected subcategory format on the public index")
    for primary_id, secondary_id, label in SUBCATEGORY.findall(block):
        if primary_id in categories:
            categories[primary_id][1].append((secondary_id, literal_eval(label).strip()))
    if not any(subcategories for _, subcategories in categories.values()):
        raise RuntimeError("No subcategories found on the public index")
    return categories


def parse_list(page, primary, secondary):
    table = page.select_one("#object_table tbody")
    if table is None:
        raise RuntimeError(f"Missing results table for {primary} / {secondary}")
    merchants = []
    for row in table.find_all("tr", recursive=False):
        if row.select_one(".dataTables_empty, .dt-empty"):
            continue
        match = MERCHANT_CLICK.fullmatch(row.get("onclick", ""))
        cells = row.find_all(["td", "th"], recursive=False)
        if not match or len(cells) < 2:
            raise RuntimeError(f"Unexpected result row for {primary} / {secondary}")
        name = clean((row.select_one(".title") or cells[0]).get_text())
        discount = clean(cells[1].get_text())
        if not name or not discount:
            raise RuntimeError(f"Unexpected result row for {primary} / {secondary}")
        merchants.append((match[1], name, discount))
    return merchants


def crawl_index(client, category, subcategory):
    stores = {}
    page = BeautifulSoup(get_index(client), "html.parser")
    hidden_fields = {
        field["name"]: field.get("value", "")
        for field in page.select("form[name=stForm] input[type=hidden][name]")
    }
    for primary_id, (primary, secondary_options) in parse_categories(page).items():
        if category and primary != category:
            continue
        if not secondary_options:
            print(f"{primary}: no searchable subcategories", flush=True)
        for secondary_id, secondary in secondary_options:
            if subcategory and secondary != subcategory:
                continue
            fields = dict(hidden_fields, main_type=primary_id, second_type_i=secondary_id,
                          second_type=secondary_id, region2="0", region="0", freeSearch="", search_method="type")
            try:
                result = BeautifulSoup(fetch(client, INDEX_URL, fields), "html.parser")
            except OSError as error:
                raise RuntimeError(f"Search request failed for {primary} / {secondary}: {error}") from error
            if result.find("font", string=NO_RESULTS):
                rows = []
            else:
                selected = {
                    field: result.select_one(f'form[name=stForm] input[name="cmb_{field}"]')
                    for field in ("main_type", "second_type_i", "region2")
                }
                if any(control is None or control.get("value") != fields[field] for field, control in selected.items()):
                    raise RuntimeError(f"Search returned the wrong filters for {primary} / {secondary}")
                rows = parse_list(result, primary, secondary)
            print(f"{primary} / {secondary}: {len(rows)}", flush=True)
            for merchant_id, name, discount in rows:
                merchant = stores.setdefault(
                    merchant_id,
                    {"id": merchant_id, "name": name, "url": OFFER_URL + merchant_id,
                     "categories": [], "discounts": []},
                )
                if discount not in merchant["discounts"]:
                    merchant["discounts"].append(discount)
                category_path = [primary, secondary]
                if category_path not in merchant["categories"]:
                    merchant["categories"].append(category_path)
            time.sleep(0.35)
    return stores


def parse_detail(html):
    doc = BeautifulSoup(html, "html.parser")
    name = doc.select_one("#pub_name")
    if not name or not clean(name.get_text(" ", strip=True)):
        raise ValueError("Offer page is missing the merchant heading")

    card = name.find_parent("div", class_="bg-light")
    description = card.select_one(".col-12.mt-3 > div:not(.hide-me) > p") if card else None
    words = WORD.findall(description.get_text(" ", strip=True).lower()) if description else []
    keywords = list(dict.fromkeys(word for word in words if word not in STOP))
    cities = list(dict.fromkeys(
        clean(city.get_text(" ", strip=True))
        for city in doc.select("ul.product-details li > div > b")
        if clean(city.get_text(" ", strip=True))
    ))
    return {"keywords": keywords[:120], "cities": cities}


def enrich_stores(client, stores):
    for index, merchant in enumerate(stores.values(), 1):
        for attempt in range(3):
            try:
                merchant.update(parse_detail(fetch(client, merchant["url"])))
                break
            except (ValueError, HTTPError) as error:
                if isinstance(error, HTTPError) and error.code in {401, 403, 429}:
                    raise RuntimeError(f"Source declined detail request: HTTP {error.code}") from error
                if attempt == 2:
                    raise RuntimeError(f"Could not read offer {merchant['id']}: {error}") from error
                time.sleep(attempt + 1)
        if index % 50 == 0 or index == len(stores):
            print(f"Offer pages: {index}/{len(stores)}", flush=True)
        time.sleep(0.35)


def write_catalog(path, stores, scope):
    data = {
        "updatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scope": scope,
        "stores": sorted(stores.values(), key=lambda store: store["name"].casefold()),
    }
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n")
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("stores.json"))
    parser.add_argument("--category", help="Restrict the crawl to one primary category")
    parser.add_argument("--subcategory", help="Restrict the crawl to one subcategory")
    args = parser.parse_args()

    client = build_opener(HTTPCookieProcessor(CookieJar()))
    client.addheaders = [("User-Agent", "hever-search/1.0 (+https://github.com/oronp/hever-search)")]
    stores = crawl_index(client, args.category, args.subcategory)
    if not stores:
        raise RuntimeError("No merchants collected. The current catalog was not replaced.")
    scope = [args.category, args.subcategory]
    if args.output.exists():
        previous = json.loads(args.output.read_text())
        if previous.get("scope") == scope and len(stores) < len(previous["stores"]) * 0.7:
            raise RuntimeError("Catalog size fell substantially. The current catalog was not replaced.")
    enrich_stores(client, stores)
    write_catalog(args.output, stores, scope)
    print(f"Saved {len(stores)} merchants to {args.output}")


if __name__ == "__main__":
    main()
