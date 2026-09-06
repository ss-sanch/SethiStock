from pathlib import Path

path = Path('sethiportfolio.py')
text = path.read_text()

helpers = r'''

THESIS_STATUSES = {"ACTIVE", "WATCH", "UNDER_REVIEW", "INVALIDATED", "CLOSED"}


def _normalise_thesis_status(value: str) -> str:
    status = str(value or "").strip().upper().replace(" ", "_")
    if status not in THESIS_STATUSES:
        raise HTTPException(status_code=400, detail=f"Invalid thesis status: {value}.")
    return status


def _thesis_rows(portfolio_id: str, admin: bool = False) -> List[Dict[str, Any]]:
    params: Dict[str, Any] = {
        "select": "id,portfolio_id,instrument_id,title,core_thesis,catalysts,key_risks,invalidation_condition,status,conviction,target_price,target_currency,opened_date,next_review_date,is_published,created_at,updated_at,instruments(id,symbol,name,currency)",
        "portfolio_id": f"eq.{portfolio_id}",
        "order": "opened_date.desc,created_at.desc",
        "limit": "100",
    }
    if not admin:
        params["is_published"] = "eq.true"
    rows = _supabase_get("portfolio_theses", params, admin=admin)
    if not rows:
        return []

    thesis_ids = [str(row["id"]) for row in rows if row.get("id")]
    update_params: Dict[str, Any] = {
        "select": "id,thesis_id,effective_date,status,conviction,summary,evidence,next_review_date,is_published,created_at",
        "thesis_id": f"in.({','.join(thesis_ids)})",
        "order": "effective_date.desc,created_at.desc",
        "limit": "500",
    }
    if not admin:
        update_params["is_published"] = "eq.true"
    updates = _supabase_get("portfolio_thesis_updates", update_params, admin=admin)
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for update in updates:
        grouped[str(update.get("thesis_id"))].append(update)

    enriched: List[Dict[str, Any]] = []
    for row in rows:
        thesis = dict(row)
        history = grouped.get(str(row.get("id")), [])
        latest = history[0] if history else None
        current_status = _normalise_thesis_status((latest or {}).get("status") or row.get("status") or "ACTIVE")
        current_conviction = int((latest or {}).get("conviction") or row.get("conviction") or 3)
        current_next_review = (latest or {}).get("next_review_date") if latest else row.get("next_review_date")
        thesis.update({
            "effective_status": current_status,
            "effective_conviction": current_conviction,
            "effective_next_review_date": current_next_review,
            "initial_conviction": int(row.get("conviction") or 3),
            "conviction_change": current_conviction - int(row.get("conviction") or 3),
            "last_review_date": (latest or {}).get("effective_date"),
            "latest_update": latest,
            "updates": history,
        })
        enriched.append(thesis)
    return enriched


def _admin_thesis(portfolio_id: str, thesis_id: str) -> Dict[str, Any]:
    rows = _supabase_get(
        "portfolio_theses",
        {
            "select": "id,portfolio_id,instrument_id,title,core_thesis,catalysts,key_risks,invalidation_condition,status,conviction,target_price,target_currency,opened_date,next_review_date,is_published,created_at,updated_at,instruments(id,symbol,name,currency)",
            "id": f"eq.{thesis_id}",
            "portfolio_id": f"eq.{portfolio_id}",
            "limit": "1",
        },
        admin=True,
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Thesis not found in this portfolio.")
    return rows[0]
'''

anchor = '\n\ndef _fx_symbol(currency: str, base_currency: str) -> str | None:'
if anchor not in text:
    raise SystemExit('thesis helper anchor not found')
text = text.replace(anchor, helpers + anchor, 1)

models = r'''

class AdminThesisPayload(BaseModel):
    symbol: str = Field(min_length=1, max_length=32)
    title: str = Field(min_length=1, max_length=200)
    core_thesis: str = Field(min_length=3, max_length=8000)
    catalysts: str = Field(default="", max_length=6000)
    key_risks: str = Field(default="", max_length=6000)
    invalidation_condition: str = Field(min_length=3, max_length=6000)
    status: str = Field(default="ACTIVE", min_length=3, max_length=32)
    conviction: int = Field(default=3, ge=1, le=5)
    target_price: Optional[float] = Field(default=None, ge=0)
    target_currency: Optional[str] = Field(default=None, min_length=3, max_length=3)
    opened_date: date
    next_review_date: Optional[date] = None
    is_published: bool = True


class AdminThesisReviewPayload(BaseModel):
    effective_date: date
    status: str = Field(min_length=3, max_length=32)
    conviction: int = Field(ge=1, le=5)
    summary: str = Field(min_length=3, max_length=4000)
    evidence: str = Field(default="", max_length=8000)
    next_review_date: Optional[date] = None
    is_published: bool = True
'''

anchor = '\n\nclass AdminTransactionCorrectionPayload(BaseModel):'
if anchor not in text:
    raise SystemExit('thesis model anchor not found')
text = text.replace(anchor, models + anchor, 1)

admin_routes = r'''

@router.get("/admin/{slug}/theses")
def get_admin_theses(slug: str, x_admin_secret: Optional[str] = Header(default=None, alias="X-Admin-Secret")) -> Dict[str, Any]:
    _require_admin(x_admin_secret)
    portfolio = _portfolio(slug)
    return {"theses": _thesis_rows(portfolio["id"], admin=True)}


@router.post("/admin/{slug}/theses")
def create_thesis(
    slug: str,
    payload: AdminThesisPayload,
    x_admin_secret: Optional[str] = Header(default=None, alias="X-Admin-Secret"),
) -> Dict[str, Any]:
    _require_admin(x_admin_secret)
    portfolio = _portfolio(slug)
    symbol = payload.symbol.strip().upper()
    instrument = _find_instrument(symbol)
    if not instrument:
        raise HTTPException(status_code=400, detail="Thesis ticker must already exist in the portfolio instrument ledger.")

    effective_transactions = _effective_transactions(_transactions(portfolio["id"]))
    book = _derive_book(effective_transactions, str(portfolio.get("base_currency") or "GBP").upper())
    if float((book.get(symbol) or {}).get("quantity") or 0.0) <= 1e-9:
        raise HTTPException(status_code=400, detail="A new thesis can only be opened for a currently held instrument.")
    if payload.opened_date > date.today():
        raise HTTPException(status_code=400, detail="Thesis opened_date cannot be in the future.")

    existing = _thesis_rows(portfolio["id"], admin=True)
    duplicate = next((row for row in existing if (row.get("instruments") or {}).get("symbol") == symbol and row.get("effective_status") != "CLOSED"), None)
    if duplicate:
        raise HTTPException(status_code=409, detail=f"{symbol} already has a non-closed thesis. Update or review that thesis instead.")

    status = _normalise_thesis_status(payload.status)
    target_currency = (payload.target_currency or instrument.get("currency") or portfolio.get("base_currency") or "GBP").upper()
    row = _supabase_post("portfolio_theses", {
        "portfolio_id": portfolio["id"],
        "instrument_id": instrument["id"],
        "title": payload.title.strip(),
        "core_thesis": payload.core_thesis.strip(),
        "catalysts": payload.catalysts.strip(),
        "key_risks": payload.key_risks.strip(),
        "invalidation_condition": payload.invalidation_condition.strip(),
        "status": status,
        "conviction": payload.conviction,
        "target_price": payload.target_price,
        "target_currency": target_currency,
        "opened_date": payload.opened_date.isoformat(),
        "next_review_date": payload.next_review_date.isoformat() if payload.next_review_date else None,
        "is_published": payload.is_published,
    })
    return {"thesis": row}


@router.patch("/admin/{slug}/theses/{thesis_id}")
def update_thesis(
    slug: str,
    thesis_id: str,
    payload: AdminThesisPayload,
    x_admin_secret: Optional[str] = Header(default=None, alias="X-Admin-Secret"),
) -> Dict[str, Any]:
    _require_admin(x_admin_secret)
    portfolio = _portfolio(slug)
    existing = _admin_thesis(portfolio["id"], thesis_id)
    symbol = payload.symbol.strip().upper()
    instrument = _find_instrument(symbol)
    if not instrument:
        raise HTTPException(status_code=400, detail="Thesis ticker must exist in the portfolio instrument ledger.")
    if str(existing.get("instrument_id")) != str(instrument.get("id")):
        raise HTTPException(status_code=400, detail="An existing thesis cannot be reassigned to a different instrument.")
    if payload.opened_date > date.today():
        raise HTTPException(status_code=400, detail="Thesis opened_date cannot be in the future.")

    target_currency = (payload.target_currency or instrument.get("currency") or portfolio.get("base_currency") or "GBP").upper()
    row = _supabase_patch(
        "portfolio_theses",
        {"id": f"eq.{thesis_id}", "portfolio_id": f"eq.{portfolio['id']}"},
        {
            "title": payload.title.strip(),
            "core_thesis": payload.core_thesis.strip(),
            "catalysts": payload.catalysts.strip(),
            "key_risks": payload.key_risks.strip(),
            "invalidation_condition": payload.invalidation_condition.strip(),
            "status": _normalise_thesis_status(payload.status),
            "conviction": payload.conviction,
            "target_price": payload.target_price,
            "target_currency": target_currency,
            "opened_date": payload.opened_date.isoformat(),
            "next_review_date": payload.next_review_date.isoformat() if payload.next_review_date else None,
            "is_published": payload.is_published,
            "updated_at": datetime.utcnow().isoformat() + "Z",
        },
    )
    return {"thesis": row}


@router.post("/admin/{slug}/theses/{thesis_id}/updates")
def create_thesis_update(
    slug: str,
    thesis_id: str,
    payload: AdminThesisReviewPayload,
    x_admin_secret: Optional[str] = Header(default=None, alias="X-Admin-Secret"),
) -> Dict[str, Any]:
    _require_admin(x_admin_secret)
    portfolio = _portfolio(slug)
    thesis = _admin_thesis(portfolio["id"], thesis_id)
    opened_date = date.fromisoformat(str(thesis.get("opened_date")))
    if payload.effective_date < opened_date:
        raise HTTPException(status_code=400, detail="A thesis review cannot predate the thesis opening date.")
    if payload.effective_date > date.today():
        raise HTTPException(status_code=400, detail="A thesis review cannot be dated in the future.")

    row = _supabase_post("portfolio_thesis_updates", {
        "thesis_id": thesis_id,
        "effective_date": payload.effective_date.isoformat(),
        "status": _normalise_thesis_status(payload.status),
        "conviction": payload.conviction,
        "summary": payload.summary.strip(),
        "evidence": payload.evidence.strip(),
        "next_review_date": payload.next_review_date.isoformat() if payload.next_review_date else None,
        "is_published": payload.is_published,
    })
    return {"update": row}
'''

anchor = '\n\n@router.post("/admin/{slug}/transaction/{transaction_id}/correct")'
if anchor not in text:
    raise SystemExit('admin thesis route anchor not found')
text = text.replace(anchor, admin_routes + anchor, 1)

public_route = r'''

@router.get("/{slug}/theses")
def get_theses(slug: str) -> Dict[str, Any]:
    portfolio = _portfolio(slug)
    return {"theses": _thesis_rows(portfolio["id"], admin=False)}
'''

anchor = '\n\n@router.get("/{slug}/performance")'
if anchor not in text:
    raise SystemExit('public thesis route anchor not found')
text = text.replace(anchor, public_route + anchor, 1)

path.write_text(text)
