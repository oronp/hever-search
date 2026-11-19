"""Build a searchable catalog from the public Hever merchant index."""

import argparse
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

INDEX_URL = "https://www.mcc.co.il/st_reshet_public.aspx"
OFFER_URL = "https://www.mcc.co.il/site/pg/st_reshet_out&p1="
LABEL = "חפש בתי עסק"
NO_RESULTS = re.compile(r"לא נמצאו|לא נמצא|אין תוצאות|אין בתי עסק|אין נתונים")
WORD = re.compile(r"[א-תA-Za-z]{3,}")
STOP = set("אשר אלא אלו האתר הינה הינו החל הזה החברה הכול הכל ועוד הוא היא ויש כמו ללא להם לכל לכם לנו כאן ניתן עוד עבור אחד אנחנו ביותר באתר מגוון מוצרים מציעה כולל כוללנית החברה מהווה בעולם בישראל הלקוחות שלנו השירות".split())


def clean(text):
    return " ".join(text.split())


def options(page, selector):
    return page.locator(f"{selector} option").evaluate_all(
        """opts => opts.filter(o => o.value && o.value !== '0' && !o.disabled)
                       .map(o => [o.value, o.textContent.trim()])"""
    )


def parse_list(page, primary, secondary):
    table = page.locator("#object_table")
    if not table.count():
        if page.get_by_text(NO_RESULTS).first.is_visible():
            return []
        raise RuntimeError(f"Missing results table for {primary} / {secondary}")
    # DataTables removes off-page rows from the document but retains their nodes.
    rows = table.evaluate(
        """table => {
            if (table.classList.contains('dataTable') && !window.jQuery?.fn?.dataTable) {
                throw new Error('The table is paginated, but its DataTables API is unavailable');
            }
            const hasTable = window.jQuery?.fn?.dataTable?.isDataTable(table);
            const allRows = hasTable
                ? window.jQuery(table).DataTable().rows().nodes().toArray()
                : [...table.tBodies[0].rows];
            return allRows.map(row => ({
                click: row.getAttribute('onclick') || '',
                placeholder: Boolean(row.querySelector('.dataTables_empty, .dt-empty')),
                name: (row.querySelector('.title') || row.cells[0])?.textContent.trim(),
                discount: row.cells[1]?.textContent.trim()
            }));
        }"""
    )
    merchants = []
    for row in rows:
        if row["placeholder"]:
            continue
        match = re.fullmatch(r"\s*mclick\(\s*['\"]?(\d+)['\"]?\s*\)\s*;?\s*", row["click"])
        if not match or not row.get("name") or not row.get("discount"):
            raise RuntimeError(f"Unexpected result row for {primary} / {secondary}")
        merchants.append((match[1], clean(row["name"]), clean(row["discount"])))
    return merchants


def crawl_index(page, category, subcategory):
    stores = {}
    page.goto(INDEX_URL, wait_until="domcontentloaded")
    page.locator("#Select1").wait_for()
    primary_options = options(page, "#Select1")
    if not primary_options:
        raise RuntimeError("No categories found on the public index")
    for primary_id, primary in primary_options:
        if category and primary != category:
            continue
        page.goto(INDEX_URL, wait_until="domcontentloaded")
        page.locator("#Select1").select_option(primary_id)
        secondary_options = options(page, "#second_type_i")
        if not secondary_options:
            print(f"{primary}: no searchable subcategories", flush=True)
        for secondary_id, secondary in secondary_options:
            if subcategory and secondary != subcategory:
                continue
            page.locator("#second_type_i").select_option(secondary_id)
            page.locator("#region2").select_option("0")
            with page.expect_navigation(wait_until="domcontentloaded") as navigation:
                page.get_by_role("button", name=LABEL, exact=True).click()
            response = navigation.value
            if response is None or not response.ok:
                raise RuntimeError(f"Search request failed for {primary} / {secondary}")
            if page.locator("#second_type_i").input_value() != secondary_id:
                raise RuntimeError(f"Search returned the wrong filter for {primary} / {secondary}")
            rows = parse_list(page, primary, secondary)
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


def enrich_stores(request, stores):
    for index, merchant in enumerate(stores.values(), 1):
        error = None
        for attempt in range(3):
            response = request.get(merchant["url"], timeout=25000, max_retries=1)
            try:
                if not response.ok or urlparse(response.url).hostname != "www.mcc.co.il":
                    raise ValueError(f"Unexpected HTTP response ({response.status})")
                merchant.update(parse_detail(response.text()))
                break
            except ValueError as exc:
                error = exc
                if response.status in {401, 403, 429}:
                    raise RuntimeError(f"Source declined detail request: HTTP {response.status}") from exc
                time.sleep(attempt + 1)
            finally:
                response.dispose()
        else:
            raise RuntimeError(f"Could not read offer {merchant['id']}: {error}")
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

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(locale="he-IL")
        page = context.new_page()
        page.set_default_timeout(25000)
        page.route("**/*", lambda route: route.abort() if route.request.resource_type in {
            "image", "font", "media",
        } else route.continue_())
        stores = crawl_index(page, args.category, args.subcategory)
        if not stores:
            raise RuntimeError("No merchants collected. The current catalog was not replaced.")
        if args.output.exists():
            previous = json.loads(args.output.read_text())
            scope = [args.category, args.subcategory]
            if previous.get("scope") == scope and len(stores) < len(previous["stores"]) * 0.7:
                raise RuntimeError("Catalog size fell substantially. The current catalog was not replaced.")
        enrich_stores(context.request, stores)
        write_catalog(args.output, stores, [args.category, args.subcategory])
    print(f"Saved {len(stores)} merchants to {args.output}")


if __name__ == "__main__":
    main()
