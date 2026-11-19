"use strict";

const fields = Object.fromEntries(
  ["query", "category", "city", "status", "updated", "results", "more"]
    .map((id) => [id, document.getElementById(id)])
);
const collator = new Intl.Collator("he", { numeric: true });
const PAGE_SIZE = 30;
const aliases = [
  ["מתנה", "מתנות", "מתנת", "שי"],
  ["מטבח", "מטבחים", "בישול", "אפייה", "אפיה", "סירים", "מחבתות"],
  ["בית", "הבית", "לבית", "עיצוב", "דקורציה", "ריהוט", "רהיטים"],
  ["נעליים", "נעלים", "הנעלה", "סניקרס"],
  ["טיפוח", "קוסמטיקה", "בישום", "בשמים", "יופי"],
  ["מחשב", "מחשבים", "לפטופ", "לפטופים", "אלקטרוניקה"],
  ["מסעדה", "מסעדות", "אוכל", "מזון"],
  ["כושר", "ספורט", "אימון", "אימונים", "קרוספיט"],
  ["טיול", "טיולים", "קמפינג", "מחנאות"],
  ["טלפון", "טלפונים", "סלולר", "סמארטפון"],
  ["קארה", "kare"],
];
const ignore = new Set(["אני", "מחפש", "מחפשת", "רוצה", "איפה", "אפשר", "לקנות", "של", "עם", "יש", "את"]);
let catalog = [];
let ready = false;
let limit = PAGE_SIZE;

function normalize(value) {
  return value.normalize("NFKD").replace(/\p{M}/gu, "").toLocaleLowerCase("he")
    .replace(/[׳״'"’]/g, "").replace(/[^\p{L}\p{N}]+/gu, " ").trim();
}

function indexStore(store) {
  const categories = [...new Set(store.categories.flat())];
  return {
    ...store,
    nameKey: normalize(store.name),
    nameTokens: new Set(normalize(store.name).split(" ")),
    categoryTokens: new Set(normalize(categories.join(" ")).split(" ")),
    cityTokens: new Set(normalize(store.cities.join(" ")).split(" ")),
    keywordTokens: new Set(store.keywords.flatMap((word) => normalize(word).split(" "))),
  };
}

function tokenScore(store, term) {
  const hits = (word) => {
    if (store.nameTokens.has(word)) return 20;
    if (store.categoryTokens.has(word)) return 9;
    if (store.cityTokens.has(word)) return 8;
    if (store.keywordTokens.has(word)) return 6;
    if (word.length >= 3 && store.nameKey.includes(word)) return 12;
    return 0;
  };
  const direct = hits(term);
  const group = aliases.find((list) => list.includes(term));
  if (!group) return direct;
  return Math.max(direct, ...group.filter((word) => word !== term).map((word) => hits(word) * .55));
}

function score(store, query, terms) {
  if (!query) return 1;
  if (store.nameKey === query) return 1000;
  if (store.nameKey.startsWith(query)) return 300;
  if (store.nameKey.includes(query)) return 150;
  if (!terms.length) return 0;
  const matches = terms.map((term) => tokenScore(store, term));
  if (matches.some((value) => value === 0)) return 0;
  return matches.reduce((sum, value) => sum + value, 0);
}

function appendOptions(select, values) {
  for (const value of [...new Set(values)].sort(collator.compare)) {
    select.add(new Option(value, value));
  }
}

function renderStore(store) {
  const row = document.createElement("li");
  row.className = "result-card";
  const text = document.createElement("div");
  const heading = document.createElement("h2");
  const link = document.createElement("a");
  link.href = store.url;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.dir = "auto";
  link.textContent = store.name;
  heading.append(link);
  text.append(heading);
  const meta = document.createElement("p");
  meta.className = "meta";
  const categories = [...new Set(store.categories.map((pair) => pair[1]))].slice(0, 3);
  const cities = store.cities.slice(0, 4);
  if (store.cities.length > cities.length) cities.push(`ועוד ${store.cities.length - cities.length}`);
  meta.textContent = [categories.join(", "), cities.join(", ")].filter(Boolean).join(" · ");
  text.append(meta);
  row.append(text);
  const discount = document.createElement("span");
  discount.className = "discount";
  discount.dir = "auto";
  discount.textContent = store.discounts.join(" / ");
  row.append(discount);
  return row;
}

function render() {
  if (!ready) return;
  const query = normalize(fields.query.value);
  const terms = [...new Set(query.split(" ").filter((term) => term && !ignore.has(term)))];
  const matches = catalog
    .filter((store) => !fields.city.value || store.cities.includes(fields.city.value))
    .filter((store) => !fields.category.value || store.categories.some(([name]) => name === fields.category.value))
    .map((store) => ({ store, rank: score(store, query, terms) }))
    .filter(({ rank }) => rank > 0)
    .sort((left, right) => right.rank - left.rank || collator.compare(left.store.name, right.store.name));
  fields.status.textContent = matches.length
    ? `${matches.length.toLocaleString("he-IL")} בתי עסק`
    : "לא נמצאו עסקים שמתאימים לחיפוש.";
  fields.results.replaceChildren(...matches.slice(0, limit).map(({ store }) => renderStore(store)));
  fields.more.hidden = matches.length <= limit;
}

async function start() {
  try {
    const response = await fetch("./stores.json", { cache: "no-cache" });
    if (!response.ok) throw new Error(`Catalog response ${response.status}`);
    const data = await response.json();
    if (!data.stores.length) throw new Error("Empty catalog");
    catalog = data.stores.map(indexStore);
    appendOptions(fields.category, catalog.flatMap((store) => store.categories.map(([name]) => name)));
    appendOptions(fields.city, catalog.flatMap((store) => store.cities));
    const updated = new Intl.DateTimeFormat("he-IL", {
      dateStyle: "medium", timeStyle: "short", timeZone: "Asia/Jerusalem"
    }).format(new Date(data.updatedAt));
    const scope = data.scope.filter(Boolean).join(" / ");
    fields.updated.textContent = scope ? `מדגם ${scope} · ${updated}` : `עודכן ${updated}`;
    ready = true;
    render();
  } catch (error) {
    fields.status.textContent = "רשימת בתי העסק אינה זמינה כרגע. אפשר לחפש באתר חבר.";
    console.error(error);
  }
}

function searchAgain() {
  limit = PAGE_SIZE;
  render();
}

fields.query.addEventListener("input", searchAgain);
fields.category.addEventListener("change", searchAgain);
fields.city.addEventListener("change", searchAgain);
fields.more.addEventListener("click", () => {
  limit += PAGE_SIZE;
  render();
});
start();
