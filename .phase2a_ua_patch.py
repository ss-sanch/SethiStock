from pathlib import Path

path = Path('sec_fundamentals.py')
text = path.read_text()


def replace_once(old, new):
    global text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'Expected one match, found {count}: {old[:120]!r}')
    text = text.replace(old, new, 1)


replace_once(
'''SEC_USER_AGENT = os.getenv(
    "SEC_USER_AGENT",
    "SethiStock/2A https://github.com/ss-sanch/SethiStock",
).strip()
''',
'''SEC_USER_AGENT = os.getenv("SEC_USER_AGENT", "").strip()
'''
)

replace_once(
'''_SEC_CACHE: Dict[str, Dict[str, Any]] = {}


def _cache_get''',
'''_SEC_CACHE: Dict[str, Dict[str, Any]] = {}


def _sec_user_agent_configured():
    """SEC asks automated clients to declare an organisation and contact email."""
    return bool(SEC_USER_AGENT and "@" in SEC_USER_AGENT and " " in SEC_USER_AGENT)


def _require_sec_user_agent():
    if not _sec_user_agent_configured():
        raise HTTPException(
            status_code=503,
            detail="SEC_USER_AGENT is not configured. Set it to an organisation name and monitored contact email before enabling SEC requests.",
        )


def _cache_get'''
)

replace_once(
'''def _sec_get_json(url: str):
    """Fetch SEC JSON with fair-access pacing and bounded retry/backoff."""
    last_error: Optional[Exception] = None
''',
'''def _sec_get_json(url: str):
    """Fetch SEC JSON with fair-access pacing and bounded retry/backoff."""
    _require_sec_user_agent()
    last_error: Optional[Exception] = None
'''
)

replace_once(
'''@router.get("/{ticker}/overview")
def sec_company_overview(ticker: str):
''',
'''@router.get("/status")
def sec_status():
    """Expose SEC readiness without leaking the configured contact address."""
    return {
        "configured": _sec_user_agent_configured(),
        "source": "SEC EDGAR Company Facts",
        "max_requests_per_second": round(1.0 / SEC_MIN_REQUEST_INTERVAL, 2),
        "ticker_map_ttl_seconds": SEC_TICKER_MAP_TTL,
        "companyfacts_ttl_seconds": SEC_COMPANYFACTS_TTL,
    }


@router.get("/{ticker}/overview")
def sec_company_overview(ticker: str):
'''
)

path.write_text(text)
