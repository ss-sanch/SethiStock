#!/usr/bin/env python3
"""Progressively seed/refresh the world's largest SethiStock snapshots without keeping Render permanently awake."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import os
import pathlib
import time
import urllib.error
import urllib.parse
import urllib.request


DEFAULT_API = "https://sethistock-api.onrender.com"
DEFAULT_SNAPSHOT_API = "https://gqqftksplktxfrilltsx.supabase.co/rest/v1/sethistock_public_snapshots"
DEFAULT_GLOBAL_UNIVERSE_CSV = "https://companiesmarketcap.com/?download=csv"


def _request(url: str, headers: dict[str, str], timeout: int = 90):
    req = urllib.request.Request(url, headers=headers)
    return urllib.request.urlopen(req, timeout=timeout)


def get_json(url: str, headers: dict[str, str], timeout: int = 90, attempts: int = 2):
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            with _request(url, headers, timeout) as response:
                return json.load(response)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ConnectionError) as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(5 * attempt)
    raise RuntimeError(f"{url}: {last_error!r}")


def get_text(url: str, headers: dict[str, str], timeout: int = 45, attempts: int = 3):
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            with _request(url, headers, timeout) as response:
                raw = response.read()
                return raw.decode("utf-8-sig", errors="replace")
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ConnectionError) as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(4 * attempt)
    raise RuntimeError(f"{url}: {last_error!r}")


def parse_timestamp(value):
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def load_fallback_universe(path: pathlib.Path):
    payload = json.loads(path.read_text(encoding="utf-8"))
    symbols = [str(symbol).strip().upper() for symbol in payload.get("symbols", [])]
    return [symbol for symbol in symbols if symbol]


def _pick_column(fieldnames, candidates):
    normalized = {
        str(name or "").strip().lower().replace(" ", "").replace("_", ""): name
        for name in fieldnames or []
    }
    for candidate in candidates:
        key = candidate.lower().replace(" ", "").replace("_", "")
        if key in normalized:
            return normalized[key]
    return None


def load_global_universe(csv_url: str, top_n: int, fallback_path: pathlib.Path):
    """Use CompaniesMarketCap's native CSV; fall back to the checked-in universe if unavailable."""
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; SethiStock snapshot warmer/2.0)",
        "Accept": "text/csv,text/plain,*/*",
    }

    try:
        text = get_text(csv_url, headers=headers, timeout=45, attempts=3)
        reader = csv.DictReader(io.StringIO(text))
        symbol_col = _pick_column(reader.fieldnames, ("Symbol", "Ticker", "Code"))
        rank_col = _pick_column(reader.fieldnames, ("Rank",))
        name_col = _pick_column(reader.fieldnames, ("Name", "Company"))
        country_col = _pick_column(reader.fieldnames, ("Country",))

        if not symbol_col:
            raise RuntimeError(f"Global universe CSV missing symbol column: {reader.fieldnames!r}")

        rows = []
        for position, row in enumerate(reader, start=1):
            symbol = str(row.get(symbol_col) or "").strip().upper()
            if not symbol:
                continue
            try:
                rank = int(float(str(row.get(rank_col) or position).replace(",", ""))) if rank_col else position
            except Exception:
                rank = position
            rows.append({
                "rank": rank,
                "ticker": symbol,
                "name": str(row.get(name_col) or "").strip() if name_col else "",
                "country": str(row.get(country_col) or "").strip() if country_col else "",
            })

        rows.sort(key=lambda item: item["rank"])
        unique = []
        seen = set()
        for row in rows:
            ticker = row["ticker"]
            if ticker in seen:
                continue
            seen.add(ticker)
            unique.append(row)
            if len(unique) >= top_n:
                break

        if len(unique) < min(top_n, 500):
            raise RuntimeError(f"Global universe returned only {len(unique)} usable tickers")

        return [row["ticker"] for row in unique], {
            "source": "companiesmarketcap_csv",
            "source_url": csv_url,
            "requested_top_n": top_n,
            "loaded": len(unique),
            "sample": unique[:10],
        }
    except Exception as exc:
        fallback = load_fallback_universe(fallback_path)
        fallback = fallback[:top_n]
        return fallback, {
            "source": "checked_in_fallback",
            "error": repr(exc),
            "requested_top_n": top_n,
            "loaded": len(fallback),
        }


def fetch_snapshot_rows(snapshot_api: str, anon_key: str):
    headers = {
        "apikey": anon_key,
        "Authorization": f"Bearer {anon_key}",
        "User-Agent": "SethiStock snapshot warmer/2.0",
    }
    params = urllib.parse.urlencode({
        "select": "ticker,analysis_updated_at,quote_updated_at,chart_updated_at",
        "limit": "5000",
    })
    rows = get_json(f"{snapshot_api}?{params}", headers=headers, timeout=30, attempts=3)
    return rows if isinstance(rows, list) else []


def choose_targets(symbols, rows, mode: str, batch_size: int, refresh_hour: int):
    by_ticker = {
        str(row.get("ticker") or "").upper(): row
        for row in rows
        if isinstance(row, dict) and row.get("ticker")
    }

    missing = [
        symbol for symbol in symbols
        if symbol not in by_ticker or not by_ticker[symbol].get("analysis_updated_at")
    ]

    if mode in {"seed", "auto"} and missing:
        return missing[:batch_size], "seed", len(missing)

    if mode == "seed":
        return [], "seed-complete", 0

    now = dt.datetime.now(dt.timezone.utc)
    if mode == "auto" and now.hour != refresh_hour:
        return [], "refresh-window-skip", 0

    epoch = dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)
    ranked = sorted(
        symbols,
        key=lambda symbol: parse_timestamp(by_ticker.get(symbol, {}).get("analysis_updated_at")) or epoch,
    )
    return ranked[:batch_size], "refresh", 0


def warm_ticker(api_url: str, ticker: str, timeout: int):
    headers = {"User-Agent": "SethiStock snapshot warmer/2.0"}
    encoded = urllib.parse.quote(ticker, safe="")
    payload = get_json(
        f"{api_url}/api/stock/{encoded}",
        headers=headers,
        timeout=timeout,
        attempts=2,
    )
    if not isinstance(payload, dict) or not payload.get("ticker"):
        raise RuntimeError("invalid stock payload")
    meta = payload.get("_meta") if isinstance(payload.get("_meta"), dict) else {}
    return {
        "requested_ticker": ticker,
        "ticker": str(payload.get("ticker") or ticker).upper(),
        "price": payload.get("current_price"),
        "cache_hit": meta.get("served_from_cache"),
        "degraded": bool(meta.get("analysis_degraded")),
        "backend_ms": meta.get("request_ms") or meta.get("analysis_ms"),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("auto", "seed", "refresh"), default="auto")
    parser.add_argument("--batch-size", type=int, default=60)
    parser.add_argument("--refresh-hour", type=int, default=5)
    parser.add_argument("--request-timeout", type=int, default=90)
    parser.add_argument("--delay-seconds", type=float, default=1.5)
    parser.add_argument("--top-n", type=int, default=600)
    parser.add_argument("--global-universe-csv", default=DEFAULT_GLOBAL_UNIVERSE_CSV)
    parser.add_argument(
        "--fallback-universe",
        default=str(pathlib.Path(__file__).resolve().parents[1] / "data" / "sethistock_seed_universe.json"),
    )
    args = parser.parse_args()

    api_url = os.getenv("SETHISTOCK_API", DEFAULT_API).rstrip("/")
    snapshot_api = os.getenv("SETHISTOCK_SNAPSHOT_API", DEFAULT_SNAPSHOT_API).rstrip("/")
    anon_key = os.getenv("SETHISTOCK_SNAPSHOT_ANON_KEY", "").strip()
    if not anon_key:
        raise SystemExit("SETHISTOCK_SNAPSHOT_ANON_KEY is required")

    symbols, universe_meta = load_global_universe(
        args.global_universe_csv,
        max(1, args.top_n),
        pathlib.Path(args.fallback_universe),
    )
    print("GLOBAL_UNIVERSE=" + json.dumps(universe_meta, separators=(",", ":")), flush=True)

    before_rows = fetch_snapshot_rows(snapshot_api, anon_key)
    targets, action, missing_before = choose_targets(
        symbols,
        before_rows,
        args.mode,
        max(1, args.batch_size),
        args.refresh_hour,
    )

    print(json.dumps({
        "universe": len(symbols),
        "snapshots_before": len(before_rows),
        "missing_before": missing_before,
        "mode": args.mode,
        "action": action,
        "targets": len(targets),
    }, separators=(",", ":")), flush=True)

    if not targets:
        print("SETHISTOCK_SNAPSHOT_WARMER_NOTHING_TO_DO", flush=True)
        return

    successes = []
    failures = []
    degraded = []

    for index, ticker in enumerate(targets, start=1):
        started = time.time()
        try:
            result = warm_ticker(api_url, ticker, args.request_timeout)
            result["seconds"] = round(time.time() - started, 2)
            result["index"] = index
            successes.append(result)
            if result.get("degraded"):
                degraded.append(ticker)
            print("WARM_OK=" + json.dumps(result, separators=(",", ":")), flush=True)
        except Exception as exc:
            failure = {
                "ticker": ticker,
                "index": index,
                "error": repr(exc),
                "seconds": round(time.time() - started, 2),
            }
            failures.append(failure)
            print("WARM_FAIL=" + json.dumps(failure, separators=(",", ":")), flush=True)

        if index < len(targets):
            time.sleep(max(0.0, args.delay_seconds))

    time.sleep(2)
    after_rows = fetch_snapshot_rows(snapshot_api, anon_key)
    after_by_ticker = {
        str(row.get("ticker") or "").upper(): row
        for row in after_rows
        if isinstance(row, dict) and row.get("ticker")
    }
    covered = sum(
        1 for symbol in symbols
        if symbol in after_by_ticker and after_by_ticker[symbol].get("analysis_updated_at")
    )

    summary = {
        "universe": len(symbols),
        "covered_after": covered,
        "coverage_pct": round((covered / len(symbols)) * 100, 1) if symbols else 0,
        "attempted": len(targets),
        "successes": len(successes),
        "degraded": len(degraded),
        "failures": len(failures),
        "universe_source": universe_meta.get("source"),
    }
    print("SETHISTOCK_SNAPSHOT_WARMER_SUMMARY=" + json.dumps(summary, separators=(",", ":")), flush=True)

    if len(failures) == len(targets):
        raise SystemExit("All snapshot warm attempts failed")


if __name__ == "__main__":
    main()
