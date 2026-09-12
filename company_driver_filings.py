"""Phase 3B SEC filing extraction for company-specific operating drivers.

SEC Company Facts intentionally excludes issuer-custom taxonomy facts and entity facts
with dimensions. Company Drivers needs both, so this module reads the actual Inline
XBRL primary documents for recent 10-K/10-Q filings and preserves contexts,
dimensions and source provenance.

This module is discovery-first: it identifies sourced candidate facts for a registry
metric. Curated extraction rules can then promote verified candidates into stable KPI
histories without manufacturing continuity.
"""

from __future__ import annotations

import math
import re
import threading
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import requests
from bs4 import BeautifulSoup
from fastapi import HTTPException

import sec_fundamentals


DRIVER_EXTRACTION_VERSION = "3b-v1"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_ARCHIVES_ROOT = "https://www.sec.gov/Archives/edgar/data"
FILING_CACHE_TTL = 6 * 60 * 60
HTML_CACHE_TTL = 6 * 60 * 60
MAX_HTML_BYTES = 12 * 1024 * 1024

_STANDARD_PREFIXES = {
    "us-gaap", "dei", "srt", "ffd", "ecd", "invest", "country", "currency",
    "exch", "naics", "sic", "stpr", "xbrli", "xbrldi", "iso4217",
}
_GENERIC_TOKENS = {
    "revenue", "revenues", "income", "operating", "company", "reported", "period",
    "segment", "services", "service", "total", "growth", "margin", "average",
    "payments", "sales", "net", "business", "under", "disclosed", "attributed",
}
_METRIC_TOKEN_ALIASES = {
    "aws_revenue": ["aws", "amazon", "web", "services"],
    "aws_operating_income": ["aws", "amazon", "web", "services"],
    "google_cloud_revenue": ["google", "cloud"],
    "google_cloud_operating_income": ["google", "cloud"],
    "google_search_revenue": ["google", "search"],
    "youtube_ads_revenue": ["youtube"],
    "data_center_revenue": ["data", "center", "datacenter"],
    "vehicle_deliveries": ["vehicle", "vehicles", "deliveries", "delivery"],
    "energy_storage_deployments": ["energy", "storage", "deployments", "deployed"],
    "reality_labs_revenue": ["reality", "labs"],
    "reality_labs_operating_income": ["reality", "labs"],
    "intelligent_cloud_revenue": ["intelligent", "cloud"],
    "microsoft_cloud_revenue": ["microsoft", "cloud"],
    "payments_volume": ["payment", "payments", "volume"],
    "processed_transactions": ["processed", "transactions", "transaction"],
}

_CACHE_LOCK = threading.Lock()
_CACHE: Dict[str, Dict[str, Any]] = {}


def _cache_get(key: str, ttl: int):
    now = time.monotonic()
    with _CACHE_LOCK:
        entry = _CACHE.get(key)
        if not entry or now - entry["at"] > ttl:
            if entry:
                _CACHE.pop(key, None)
            return None
        return entry["value"]


def _cache_set(key: str, value: Any):
    with _CACHE_LOCK:
        _CACHE[key] = {"at": time.monotonic(), "value": value}
    return value


def _sec_get_text(url: str) -> str:
    """Fetch one SEC document using the Phase 2A fair-access configuration."""
    sec_fundamentals._require_sec_user_agent()
    last_error: Optional[Exception] = None
    for attempt in range(sec_fundamentals.SEC_MAX_RETRIES + 1):
        try:
            sec_fundamentals._wait_for_rate_slot()
            response = requests.get(
                url,
                headers=sec_fundamentals._SEC_HEADERS,
                timeout=max(sec_fundamentals.SEC_REQUEST_TIMEOUT, 15.0),
            )
            if response.status_code == 404:
                raise HTTPException(status_code=404, detail="SEC filing document not found.")
            if response.status_code == 429 or response.status_code >= 500:
                if attempt < sec_fundamentals.SEC_MAX_RETRIES:
                    time.sleep(min(0.75 * (2 ** attempt), 6.0))
                    continue
            if response.status_code >= 400:
                raise HTTPException(status_code=502, detail=f"SEC filing request failed with status {response.status_code}.")
            if len(response.content) > MAX_HTML_BYTES:
                raise HTTPException(status_code=413, detail="SEC filing document exceeds the Phase 3B parser size limit.")
            return response.text
        except HTTPException:
            raise
        except Exception as exc:
            last_error = exc
            if attempt < sec_fundamentals.SEC_MAX_RETRIES:
                time.sleep(0.75 * (2 ** attempt))
                continue
    raise HTTPException(status_code=502, detail=f"SEC filing document temporarily unavailable: {last_error}")


def get_submission_history(ticker: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    identity = sec_fundamentals.resolve_sec_identity(ticker)
    cik = identity["cik_padded"]
    key = f"submissions:{cik}"
    cached = _cache_get(key, FILING_CACHE_TTL)
    if cached is None:
        cached = sec_fundamentals._sec_get_json(SEC_SUBMISSIONS_URL.format(cik=cik))
        if not isinstance(cached, dict) or not isinstance(cached.get("filings"), dict):
            raise HTTPException(status_code=502, detail="SEC submissions payload was malformed.")
        _cache_set(key, cached)
    return identity, cached


def recent_periodic_filings(ticker: str, limit: int = 8, forms: Sequence[str] = ("10-K", "10-Q")) -> List[Dict[str, Any]]:
    identity, submissions = get_submission_history(ticker)
    recent = ((submissions.get("filings") or {}).get("recent") or {})
    columns = [
        "accessionNumber", "filingDate", "reportDate", "form", "primaryDocument",
        "isXBRL", "isInlineXBRL", "acceptanceDateTime",
    ]
    arrays = {name: recent.get(name) if isinstance(recent.get(name), list) else [] for name in columns}
    count = max((len(values) for values in arrays.values()), default=0)
    allowed = {str(form).upper() for form in forms}
    out: List[Dict[str, Any]] = []
    for index in range(count):
        row = {name: values[index] if index < len(values) else None for name, values in arrays.items()}
        form = str(row.get("form") or "").upper()
        if form not in allowed:
            continue
        accession = str(row.get("accessionNumber") or "").strip()
        document = str(row.get("primaryDocument") or "").strip()
        if not accession or not document:
            continue
        accession_compact = accession.replace("-", "")
        source_url = f"{SEC_ARCHIVES_ROOT}/{int(identity['cik'])}/{accession_compact}/{document}"
        out.append({
            "ticker": identity["ticker"],
            "cik": identity["cik"],
            "form": form,
            "filing_date": row.get("filingDate"),
            "report_date": row.get("reportDate"),
            "accepted_at": row.get("acceptanceDateTime"),
            "accession": accession,
            "primary_document": document,
            "is_xbrl": bool(row.get("isXBRL")),
            "is_inline_xbrl": bool(row.get("isInlineXBRL")),
            "source_url": source_url,
        })
        if len(out) >= limit:
            break
    return out


def _tag_suffix(tag: Any) -> str:
    name = str(getattr(tag, "name", "") or "").lower()
    return name.split(":")[-1]


def _context_map(soup: BeautifulSoup) -> Dict[str, Dict[str, Any]]:
    contexts: Dict[str, Dict[str, Any]] = {}
    for tag in soup.find_all(lambda item: _tag_suffix(item) == "context"):
        context_id = str(tag.get("id") or "").strip()
        if not context_id:
            continue
        start = end = instant = None
        dimensions = []
        for child in tag.find_all(True):
            suffix = _tag_suffix(child)
            text = child.get_text(" ", strip=True)
            if suffix == "startdate":
                start = text or None
            elif suffix == "enddate":
                end = text or None
            elif suffix == "instant":
                instant = text or None
            elif suffix == "explicitmember":
                dimensions.append({
                    "dimension": str(child.get("dimension") or "").strip() or None,
                    "member": text or None,
                })
            elif suffix == "typedmember":
                dimensions.append({
                    "dimension": str(child.get("dimension") or "").strip() or None,
                    "member": text or None,
                    "typed": True,
                })
        contexts[context_id] = {
            "start": start,
            "end": end or instant,
            "instant": instant,
            "period_semantics": "instant" if instant else "duration",
            "dimensions": dimensions,
        }
    return contexts


def _normalise_numeric_text(text: str) -> Optional[float]:
    raw = str(text or "").strip()
    if not raw or raw in {"-", "—", "–"}:
        return None
    negative = raw.startswith("(") and raw.endswith(")")
    cleaned = raw.replace("\u00a0", " ")
    cleaned = re.sub(r"[$£€¥,%\s,]", "", cleaned)
    cleaned = cleaned.strip("()")
    if not cleaned or not re.fullmatch(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", cleaned):
        return None
    try:
        value = float(cleaned)
    except Exception:
        return None
    return -abs(value) if negative else value


def _inline_fact_value(tag: Any) -> Tuple[Any, bool]:
    if str(tag.get("xsi:nil") or tag.get("nil") or "").lower() == "true":
        return None, False
    text = tag.get_text(" ", strip=True)
    if _tag_suffix(tag) == "nonnumeric":
        return text or None, False
    value = _normalise_numeric_text(text)
    if value is None:
        return text or None, False
    try:
        scale = int(str(tag.get("scale") or "0"))
    except Exception:
        scale = 0
    if scale:
        value *= 10 ** scale
    sign = str(tag.get("sign") or "").strip()
    if sign == "-":
        value = -abs(value)
    if not math.isfinite(value):
        return None, False
    return value, True


def parse_inline_xbrl(html: str, filing: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Parse Inline XBRL facts while preserving custom dimensions and filing provenance."""
    soup = BeautifulSoup(html, "html.parser")
    contexts = _context_map(soup)
    facts: List[Dict[str, Any]] = []
    prefixes = set()
    custom_prefixes = set()

    for tag in soup.find_all(lambda item: _tag_suffix(item) in {"nonfraction", "nonnumeric"}):
        qualified = str(tag.get("name") or "").strip()
        context_ref = str(tag.get("contextref") or "").strip()
        if not qualified or not context_ref:
            continue
        if ":" in qualified:
            prefix, concept = qualified.split(":", 1)
        else:
            prefix, concept = "", qualified
        prefix = prefix.lower()
        prefixes.add(prefix)
        if prefix and prefix not in _STANDARD_PREFIXES:
            custom_prefixes.add(prefix)
        value, numeric = _inline_fact_value(tag)
        context = contexts.get(context_ref) or {
            "start": None, "end": None, "instant": None,
            "period_semantics": None, "dimensions": [],
        }
        facts.append({
            "taxonomy": prefix or None,
            "concept": concept,
            "qualified_concept": qualified,
            "context_ref": context_ref,
            "unit_ref": str(tag.get("unitref") or "").strip() or None,
            "value": value,
            "numeric": numeric,
            "start": context.get("start"),
            "end": context.get("end"),
            "instant": context.get("instant"),
            "period_semantics": context.get("period_semantics"),
            "dimensions": context.get("dimensions") or [],
            "filing_date": (filing or {}).get("filing_date"),
            "report_date": (filing or {}).get("report_date"),
            "form": (filing or {}).get("form"),
            "accession": (filing or {}).get("accession"),
            "source_url": (filing or {}).get("source_url"),
            "extraction_method": "sec_inline_xbrl",
        })

    return {
        "fact_count": len(facts),
        "context_count": len(contexts),
        "taxonomies": sorted(prefix for prefix in prefixes if prefix),
        "custom_taxonomies": sorted(custom_prefixes),
        "facts": facts,
    }


def get_parsed_filing(filing: Dict[str, Any]) -> Dict[str, Any]:
    url = filing["source_url"]
    key = f"filing:{url}"
    cached = _cache_get(key, HTML_CACHE_TTL)
    if cached is not None:
        return cached
    html = _sec_get_text(url)
    parsed = parse_inline_xbrl(html, filing=filing)
    return _cache_set(key, parsed)


def _tokenise(value: str) -> List[str]:
    # Split snake/camel/punctuation forms into stable lowercase search tokens.
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(value or ""))
    return [token.lower() for token in re.findall(r"[A-Za-z0-9]+", text)]


def metric_tokens(metric: Dict[str, Any]) -> List[str]:
    tokens: List[str] = []
    key = str(metric.get("key") or "").strip().lower()
    sources = [metric.get("key"), metric.get("label"), metric.get("category")]
    sources.extend(_METRIC_TOKEN_ALIASES.get(key, []))
    for source in sources:
        for token in _tokenise(str(source or "")):
            if len(token) >= 3 and token not in _GENERIC_TOKENS and token not in tokens:
                tokens.append(token)
    return tokens


def _dimension_text(fact: Dict[str, Any]) -> str:
    parts = []
    for dimension in fact.get("dimensions") or []:
        parts.extend([str(dimension.get("dimension") or ""), str(dimension.get("member") or "")])
    return " ".join(parts)


def score_fact_for_metric(fact: Dict[str, Any], metric: Dict[str, Any]) -> int:
    tokens = metric_tokens(metric)
    if not tokens:
        return 0
    concept_text = " ".join([
        str(fact.get("taxonomy") or ""),
        str(fact.get("concept") or ""),
        str(fact.get("qualified_concept") or ""),
    ]).lower()
    dimension_text = _dimension_text(fact).lower()
    score = 0
    for token in tokens:
        if token in concept_text:
            score += 4
        if token in dimension_text:
            score += 6
    expected_semantics = metric.get("period_semantics")
    if expected_semantics and fact.get("period_semantics") == expected_semantics:
        score += 2
    if fact.get("numeric"):
        score += 1
    if fact.get("taxonomy") and fact.get("taxonomy") not in _STANDARD_PREFIXES:
        score += 2
    return score


def discover_metric_candidates(
    ticker: str,
    metric: Dict[str, Any],
    filing_limit: int = 4,
    candidate_limit: int = 80,
) -> Dict[str, Any]:
    filings = recent_periodic_filings(ticker, limit=max(1, min(int(filing_limit), 12)))
    candidates: List[Dict[str, Any]] = []
    filing_summaries = []
    for filing in filings:
        if filing.get("is_inline_xbrl") is False:
            continue
        parsed = get_parsed_filing(filing)
        filing_summaries.append({
            **filing,
            "fact_count": parsed["fact_count"],
            "context_count": parsed["context_count"],
            "custom_taxonomies": parsed["custom_taxonomies"],
        })
        for fact in parsed["facts"]:
            score = score_fact_for_metric(fact, metric)
            if score <= 0:
                continue
            row = dict(fact)
            row["score"] = score
            candidates.append(row)

    # Keep the strongest sourced candidates first. Date and concept make ties deterministic.
    candidates.sort(
        key=lambda row: (
            int(row.get("score") or 0),
            str(row.get("filing_date") or ""),
            str(row.get("end") or ""),
            str(row.get("qualified_concept") or ""),
        ),
        reverse=True,
    )
    candidates = candidates[: max(1, min(int(candidate_limit), 500))]
    return {
        "extraction_version": DRIVER_EXTRACTION_VERSION,
        "ticker": str(ticker).strip().upper(),
        "metric": metric.get("key"),
        "metric_label": metric.get("label"),
        "tokens": metric_tokens(metric),
        "filings_checked": filing_summaries,
        "candidate_count": len(candidates),
        "candidates": candidates,
        "data_state": "discovery_candidates",
        "rule": "Candidates are sourced filing facts, not yet a curated continuous KPI series.",
    }


def extraction_schema() -> Dict[str, Any]:
    return {
        "version": DRIVER_EXTRACTION_VERSION,
        "phase": "3B",
        "source": "SEC EDGAR Inline XBRL primary filings",
        "supported_forms": ["10-K", "10-Q"],
        "standard_prefixes": sorted(_STANDARD_PREFIXES),
        "candidate_contract": {
            "identity": ["taxonomy", "concept", "qualified_concept", "context_ref"],
            "period": ["start", "end", "instant", "period_semantics"],
            "dimensions": "Preserved from xbrli:context explicit/typed members.",
            "provenance": ["form", "filing_date", "report_date", "accession", "source_url", "extraction_method"],
        },
        "promotion_rule": "A discovery candidate becomes a stable KPI series only after a ticker/metric extraction rule has been verified against filings.",
    }
