from pathlib import Path

filings = Path('company_driver_filings.py')
text = filings.read_text(encoding='utf-8')
anchor = '\n\ndef extraction_schema() -> Dict[str, Any]:\n'
assert anchor in text
insert = r'''


def search_filing_facts(
    ticker: str,
    query: str,
    filing_limit: int = 2,
    result_limit: int = 100,
) -> Dict[str, Any]:
    """Search parsed Inline XBRL fact names/dimensions for deterministic rule research."""
    terms = [token for token in _tokenise(str(query or "")) if len(token) >= 2]
    if not terms:
        raise ValueError("query must contain at least one searchable term")
    filings = recent_periodic_filings(ticker, limit=max(1, min(int(filing_limit), 12)))
    matches: List[Dict[str, Any]] = []
    filing_summaries = []
    for filing in filings:
        if filing.get("is_inline_xbrl") is False:
            continue
        parsed = get_parsed_filing(filing)
        filing_summaries.append({
            "form": filing.get("form"),
            "filing_date": filing.get("filing_date"),
            "report_date": filing.get("report_date"),
            "accession": filing.get("accession"),
            "source_url": filing.get("source_url"),
        })
        for fact in parsed.get("facts") or []:
            if not isinstance(fact, dict):
                continue
            haystack = " ".join([
                str(fact.get("qualified_concept") or ""),
                _dimension_text(fact),
            ]).lower()
            compact_haystack = _compact_search_text(haystack)
            if not all(_compact_search_text(term) in compact_haystack for term in terms):
                continue
            matches.append(dict(fact))
    matches.sort(
        key=lambda row: (
            str(row.get("filing_date") or ""),
            str(row.get("end") or row.get("instant") or ""),
            str(row.get("qualified_concept") or ""),
        ),
        reverse=True,
    )
    matches = matches[: max(1, min(int(result_limit), 500))]
    return {
        "extraction_version": DRIVER_EXTRACTION_VERSION,
        "ticker": str(ticker).strip().upper(),
        "query": query,
        "terms": terms,
        "filings_checked": filing_summaries,
        "match_count": len(matches),
        "matches": matches,
        "data_state": "raw_filing_fact_search",
    }


def _compact_search_text(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())
'''
text = text.replace(anchor, insert + anchor, 1)
filings.write_text(text, encoding='utf-8')

drivers = Path('company_drivers.py')
dtext = drivers.read_text(encoding='utf-8')
route_anchor = '\n\n@router.get("/history/schema")\n'
assert route_anchor in dtext
route = r'''

@router.get("/{ticker}/facts/search")
def company_driver_fact_search(
    ticker: str,
    q: str = Query(..., min_length=2, max_length=120),
    filings: int = Query(2, ge=1, le=12),
    limit: int = Query(100, ge=1, le=500),
):
    """Search raw Inline XBRL concepts/dimensions to research verified KPI rules."""
    try:
        registry = get_company_driver_registry(ticker)
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail=f"Company Drivers registry is not yet available for {str(ticker).strip().upper()}.",
        ) from exc
    try:
        result = company_driver_filings.search_filing_facts(
            registry["ticker"], q, filing_limit=filings, result_limit=limit
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    result.update({
        "company": registry["company"],
        "registry_schema_version": DRIVER_SCHEMA_VERSION,
    })
    return result
'''
dtext = dtext.replace(route_anchor, route + route_anchor, 1)
drivers.write_text(dtext, encoding='utf-8')
print('PHASE3C_FACT_SEARCH_PATCHED')
