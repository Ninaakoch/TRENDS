#!/usr/bin/env python3
"""Collect Google Trends interest for the products in config.json -> data/trends.json.

Google Trends compares at most 5 terms per request and scales every request to its
own peak. To rank more products than that, each request includes the same reference
product (the "anchor"). normalize() rescales the other terms so the anchor has the
same average in every request, which puts all products on one scale.

Backends:
  - pytrends (default): free, unofficial, archived, often blocked by Google.
  - SerpApi: used automatically when the SERPAPI_KEY environment variable is set.
"""

import json
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
OUTPUT_PATH = ROOT / "data" / "trends.json"

MAX_TERMS = 5            # Google Trends limit per comparison
MIN_RAW_VALUE = 5        # below this, Google's integer rounding makes a curve unreliable
RETRY_WAITS = (60, 180)  # seconds to wait before the 2nd and 3rd attempt
PAUSE = (6, 12)          # random pause (seconds) before each request


# ---------------------------------------------------------------- configuration

def term_for(product, geo):
    terms = product["terms"]
    return terms.get(geo) or terms.get("default")


def load_config():
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    keys = [p["key"] for p in cfg["products"]]
    if len(set(keys)) != len(keys):
        sys.exit("config.json: product keys must be unique")
    if cfg["anchor"] not in keys:
        sys.exit("config.json: 'anchor' must match one product key")
    anchor = next(p for p in cfg["products"] if p["key"] == cfg["anchor"])
    for geo in cfg["countries"]:
        if not term_for(anchor, geo):
            sys.exit(f"config.json: the anchor product needs a term for {geo}")
        used = [term_for(p, geo).lower() for p in cfg["products"] if term_for(p, geo)]
        if len(set(used)) != len(used):
            sys.exit(f"config.json: two products share the same term in {geo}")
    return cfg


# ---------------------------------------------------------------- data sources

def make_fetcher(timeframe):
    """Return (backend name, fetch). fetch(terms, geo) -> (dates, {term: [values]})."""
    api_key = os.environ.get("SERPAPI_KEY", "").strip()

    if api_key:
        import requests

        def fetch(terms, geo):
            response = requests.get(
                "https://serpapi.com/search.json",
                params={
                    "engine": "google_trends",
                    "q": ",".join(terms),
                    "geo": geo,
                    "date": timeframe,
                    "data_type": "TIMESERIES",
                    "api_key": api_key,
                },
                timeout=60,
            )
            response.raise_for_status()
            rows = response.json()["interest_over_time"]["timeline_data"]
            dates, data = [], {t: [] for t in terms}
            for row in rows:
                if row.get("partial_data"):
                    continue  # the current, unfinished week
                day = datetime.fromtimestamp(int(row["timestamp"]), timezone.utc)
                dates.append(day.strftime("%Y-%m-%d"))
                by_query = {v["query"]: v["extracted_value"] for v in row["values"]}
                for t in terms:
                    data[t].append(float(by_query[t]))
            if not dates:
                raise RuntimeError("SerpApi returned no data")
            return dates, data

        return "serpapi", fetch

    from pytrends.request import TrendReq

    pytrends = TrendReq(hl="en-US", tz=0, timeout=(10, 30))

    def fetch(terms, geo):
        pytrends.build_payload(terms, cat=0, timeframe=timeframe, geo=geo)
        df = pytrends.interest_over_time()
        if df.empty:
            raise RuntimeError("Google Trends returned no data")
        if "isPartial" in df.columns:  # the current, unfinished week
            df = df[df["isPartial"].astype(str).str.lower() != "true"]
        dates = [d.strftime("%Y-%m-%d") for d in df.index]
        return dates, {t: [float(v) for v in df[t]] for t in terms}

    return "pytrends", fetch


def with_retries(fn, *args):
    last = None
    for attempt in range(len(RETRY_WAITS) + 1):
        try:
            return fn(*args)
        except Exception as exc:  # network error, 429, empty answer...
            last = exc
            if attempt < len(RETRY_WAITS):
                wait = RETRY_WAITS[attempt]
                print(f"  attempt {attempt + 1} failed ({str(exc)[:120]}); waiting {wait}s", flush=True)
                time.sleep(wait)
    raise last


# ---------------------------------------------------------------- computation

def mean(values):
    return sum(values) / len(values)


def normalize(batches, anchor):
    """Put several Google Trends requests on one scale.

    batches: list of (dates, {term: [values]}); every request contains `anchor`.
    Returns (dates, {term: [values, overall peak = 100]}, weak_terms).
    """
    dates, first = batches[0]
    reference = mean(first[anchor])
    if reference <= 0:
        raise ValueError("the reference product has no search data here; pick another anchor in config.json")

    scaled = {anchor: list(first[anchor])}
    weak = []
    for batch_dates, data in batches:
        if len(batch_dates) != len(dates):
            raise ValueError("requests returned a different number of weeks")
        level = mean(data[anchor])
        if level <= 0:
            raise ValueError("the reference product has no search data in one request")
        factor = reference / level
        for term, values in data.items():
            if term == anchor:
                continue
            scaled[term] = [v * factor for v in values]
            if level < MIN_RAW_VALUE or max(values) < MIN_RAW_VALUE:
                weak.append(term)

    peak = max(max(v) for v in scaled.values())
    final = {t: [v / peak * 100 for v in vals] for t, vals in scaled.items()}
    return dates, final, weak


def compute_stats(series, recent_weeks):
    """level = average of the last N weeks; change = % versus the N weeks before."""
    out = {}
    for key, values in series.items():
        if not values:
            continue
        recent = values[-recent_weeks:]
        level = mean(recent)
        previous = values[-2 * recent_weeks:-recent_weeks]
        change = None
        if len(previous) == recent_weeks and mean(previous) > 0:
            change = (level / mean(previous) - 1) * 100
        out[key] = {
            "level": round(level, 1),
            "change": None if change is None else round(change, 1),
        }
    return out


def collect_country(cfg, geo, fetch):
    products = cfg["products"]
    anchor = next(p for p in products if p["key"] == cfg["anchor"])
    anchor_term = term_for(anchor, geo)
    others = [p for p in products if p is not anchor and term_for(p, geo)]
    term_to_product = {term_for(p, geo): p for p in [anchor] + others}

    size = MAX_TERMS - 1  # one slot is taken by the anchor
    chunks = [others[i:i + size] for i in range(0, len(others), size)] or [[]]

    batches = []
    for chunk in chunks:
        time.sleep(random.uniform(*PAUSE))
        terms = [anchor_term] + [term_for(p, geo) for p in chunk]
        batches.append(with_retries(fetch, terms, geo))

    dates, scaled, weak = normalize(batches, anchor_term)
    series = {term_to_product[t]["key"]: [round(v, 1) for v in vals] for t, vals in scaled.items()}

    warnings = []
    if weak:
        labels = ", ".join(term_to_product[t]["label"] for t in weak)
        warnings.append(f"{labels}: small next to the reference product, so these curves are approximate.")

    return {
        "dates": dates,
        "series": series,
        "stats": compute_stats(series, cfg["recent_weeks"]),
        "terms": {p["key"]: term_for(p, geo) for p in [anchor] + others},
        "warnings": warnings,
    }


def build_europe(countries, recent_weeks):
    """Average of the market indices (each market is already scaled to its own peak = 100)."""
    usable = [c for c in countries.values() if c.get("series") and c.get("dates")]
    if not usable:
        return None
    n = min(len(c["dates"]) for c in usable)
    keys = []
    for c in usable:
        keys += [k for k in c["series"] if k not in keys]

    averaged = {}
    for key in keys:
        columns = [c["series"][key][-n:] for c in usable if key in c["series"]]
        averaged[key] = [mean([col[i] for col in columns]) for i in range(n)]
    peak = max(max(v) for v in averaged.values())
    if peak <= 0:
        return None
    series = {k: [round(x / peak * 100, 1) for x in v] for k, v in averaged.items()}

    return {
        "name": "Europe",
        "status": "ok",
        "message": "",
        "dates": usable[0]["dates"][-n:],
        "series": series,
        "stats": compute_stats(series, recent_weeks),
        "terms": {},
        "warnings": [],
        "markets": [c["name"] for c in usable],
    }


# ---------------------------------------------------------------- main

def load_previous():
    try:
        return json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def main():
    cfg = load_config()
    backend, fetch = make_fetcher(cfg["timeframe"])
    previous = load_previous()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    countries, refreshed = {}, 0
    for geo, name in cfg["countries"].items():
        print(f"{name} ({geo})", flush=True)
        try:
            entry = collect_country(cfg, geo, fetch)
            entry.update(name=name, status="ok", message="", updated=now)
            refreshed += 1
        except Exception as exc:
            message = str(exc)[:200]
            print(f"  failed: {message}", flush=True)
            old = previous.get("countries", {}).get(geo)
            if old and old.get("series"):
                entry = {**old, "name": name, "status": "stale", "message": message}
            else:
                entry = {
                    "name": name, "status": "error", "message": message, "updated": None,
                    "dates": [], "series": {}, "stats": {}, "terms": {}, "warnings": [],
                }
        countries[geo] = entry

    if refreshed == 0:
        print("No market could be refreshed; data/trends.json left unchanged.", file=sys.stderr)
        return 1

    output = {
        "generated": now,
        "backend": backend,
        "timeframe": cfg["timeframe"],
        "recent_weeks": cfg["recent_weeks"],
        "anchor": cfg["anchor"],
        "products": [{"key": p["key"], "label": p["label"]} for p in cfg["products"]],
        "europe": build_europe(countries, cfg["recent_weeks"]),
        "countries": countries,
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUTPUT_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(output, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(OUTPUT_PATH)
    print(f"Done: {refreshed}/{len(cfg['countries'])} markets refreshed ({backend}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
