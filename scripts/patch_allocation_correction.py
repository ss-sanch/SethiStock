from pathlib import Path

p = Path('sethiportfolio.py')
s = p.read_text(encoding='utf-8')

# 1) Add paired allocation-correction payload.
model_marker = '''class AdminActiveAllocationPayload(BaseModel):\n    trade_date: date\n    funding_symbol: str = Field(min_length=1, max_length=32)\n    funding_quantity: float = Field(gt=0)\n    funding_price: float = Field(ge=0)\n    funding_fees: float = Field(default=0, ge=0)\n    target_symbol: str = Field(min_length=1, max_length=32)\n    target_name: str = Field(min_length=1, max_length=160)\n    target_asset_type: str = Field(default="equity", min_length=1, max_length=40)\n    target_currency: str = Field(default="GBP", min_length=3, max_length=3)\n    target_quantity: float = Field(gt=0)\n    target_price: float = Field(ge=0)\n    target_fees: float = Field(default=0, ge=0)\n    target_fx_rate_to_base: Optional[float] = Field(default=None, gt=0)\n    reason: str = Field(min_length=3, max_length=1000)\n\n\n'''
if model_marker not in s:
    raise SystemExit('active allocation model marker missing')
model_add = model_marker + '''class AdminAllocationCorrectionPayload(BaseModel):\n    corrected_trade_date: date\n    funding_quantity: float = Field(gt=0)\n    funding_price: float = Field(ge=0)\n    funding_fees: float = Field(default=0, ge=0)\n    target_quantity: float = Field(gt=0)\n    target_price: float = Field(ge=0)\n    target_fees: float = Field(default=0, ge=0)\n    target_fx_rate_to_base: Optional[float] = Field(default=None, gt=0)\n    reason: str = Field(min_length=3, max_length=1000)\n\n\n'''
s = s.replace(model_marker, model_add, 1)

# 2) Add immutable-ledger projection + allocation correction builders.
helper_marker = '''@router.get("/admin/{slug}/instrument-lookup")\ndef admin_instrument_lookup(\n'''
if helper_marker not in s:
    raise SystemExit('instrument lookup marker missing')
helpers = r'''_ALLOCATION_NOTE_RE = re.compile(r"^ALLOCATION ([a-f0-9]{12}) (FUNDING|TARGET):\s*(.*)$", re.IGNORECASE)
_ALLOCATION_REVERSAL_RE = re.compile(r"^ALLOCATION-REVERSAL ([a-f0-9]{12}) ORIGINAL ([a-f0-9]{12}) (FUNDING|TARGET):\s*(.*)$", re.IGNORECASE)
_ALLOCATION_CORRECTS_RE = re.compile(r"^\[CORRECTS ([a-f0-9]{12})\]\s*(.*)$", re.IGNORECASE)


def _allocation_note(txn: Dict[str, Any]) -> Optional[Dict[str, str]]:
    match = _ALLOCATION_NOTE_RE.match(str(txn.get("note") or ""))
    if not match:
        return None
    return {"decision_id": match.group(1).lower(), "leg": match.group(2).upper(), "reason": match.group(3).strip()}


def _allocation_reversal_note(txn: Dict[str, Any]) -> Optional[Dict[str, str]]:
    match = _ALLOCATION_REVERSAL_RE.match(str(txn.get("note") or ""))
    if not match:
        return None
    return {
        "correction_id": match.group(1).lower(),
        "original_decision_id": match.group(2).lower(),
        "leg": match.group(3).upper(),
        "reason": match.group(4).strip(),
    }


def _effective_transactions(transactions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Project the immutable audit ledger into the economic/public transaction history.

    Allocation corrections keep the original and reversal rows privately, while this
    projection removes the superseded allocation and its reversal rows and retains
    only the corrected replacement allocation.
    """
    superseded = {
        meta["original_decision_id"]
        for txn in transactions
        if (meta := _allocation_reversal_note(txn)) is not None
    }
    projected: List[tuple[str, int, Dict[str, Any]]] = []
    for index, txn in enumerate(transactions):
        if _allocation_reversal_note(txn):
            continue
        allocation = _allocation_note(txn)
        if allocation and allocation["decision_id"] in superseded:
            continue
        row = dict(txn)
        if allocation:
            reason = allocation["reason"]
            corrected = _ALLOCATION_CORRECTS_RE.match(reason)
            if corrected:
                reason = corrected.group(2).strip()
                row["note"] = f"ALLOCATION {allocation['decision_id']} {allocation['leg']}: {reason}"
        projected.append((str(row.get("trade_date") or ""), index, row))
    projected.sort(key=lambda item: (item[0], item[1]))
    return [row for _, _, row in projected]


def _find_allocation(transactions: List[Dict[str, Any]], decision_id: str) -> tuple[Dict[str, Any], Dict[str, Any]]:
    decision_id = decision_id.strip().lower()
    if not re.fullmatch(r"[a-f0-9]{12}", decision_id):
        raise HTTPException(status_code=400, detail="Invalid allocation decision ID.")
    if any(
        meta and meta["original_decision_id"] == decision_id
        for meta in (_allocation_reversal_note(txn) for txn in transactions)
    ):
        raise HTTPException(status_code=409, detail="This allocation has already been superseded by an allocation correction.")
    legs: Dict[str, Dict[str, Any]] = {}
    for txn in transactions:
        meta = _allocation_note(txn)
        if meta and meta["decision_id"] == decision_id:
            if meta["leg"] in legs:
                raise HTTPException(status_code=409, detail=f"Allocation {decision_id} has duplicate {meta['leg']} rows.")
            legs[meta["leg"]] = txn
    if set(legs) != {"FUNDING", "TARGET"}:
        raise HTTPException(status_code=404, detail="A complete paired allocation was not found for this decision ID.")
    funding, target = legs["FUNDING"], legs["TARGET"]
    if str(funding.get("side") or "").upper() != "SELL" or str(target.get("side") or "").upper() != "BUY":
        raise HTTPException(status_code=409, detail="Allocation legs do not have the expected SELL funding / BUY target structure.")
    if funding.get("trade_date") != target.get("trade_date"):
        raise HTTPException(status_code=409, detail="Allocation legs do not share the same original trade date.")
    return funding, target


def _validate_effective_ledger(portfolio: Dict[str, Any], raw_transactions: List[Dict[str, Any]]) -> Dict[str, Any]:
    effective = _effective_transactions(raw_transactions)
    base_currency = str(portfolio.get("base_currency") or "GBP").upper()
    _derive_book(effective, base_currency)
    cash = float(portfolio.get("initial_capital") or 0.0)
    minimum_cash = cash
    for txn in effective:
        qty = float(txn.get("quantity") or 0.0)
        price = float(txn.get("price") or 0.0)
        fees = float(txn.get("fees") or 0.0)
        fx = _trade_fx(txn, base_currency)
        if str(txn.get("side") or "").upper() == "BUY":
            cash -= (qty * price + fees) * fx
        else:
            cash += (qty * price - fees) * fx
        minimum_cash = min(minimum_cash, cash)
        if cash < -0.01:
            raise HTTPException(status_code=400, detail=f"Allocation correction would create negative historical cash ({cash:.2f} {base_currency}).")
    return {"effective_transactions": effective, "ending_cash": cash, "minimum_cash": minimum_cash, "base_currency": base_currency}


def _build_allocation_correction(
    portfolio: Dict[str, Any],
    raw_transactions: List[Dict[str, Any]],
    decision_id: str,
    payload: AdminAllocationCorrectionPayload,
    correction_id: str,
) -> Dict[str, Any]:
    funding, target = _find_allocation(raw_transactions, decision_id)
    original_date = date.fromisoformat(str(funding["trade_date"]))
    inception_date = date.fromisoformat(str(portfolio.get("inception_date")))
    if payload.corrected_trade_date < inception_date:
        raise HTTPException(status_code=400, detail="Corrected trade date cannot precede portfolio inception.")
    if payload.corrected_trade_date > date.today():
        raise HTTPException(status_code=400, detail="Corrected trade date cannot be in the future.")
    if payload.corrected_trade_date == original_date:
        raise HTTPException(status_code=400, detail="Corrected trade date must differ from the original allocation date.")

    base_currency = str(portfolio.get("base_currency") or "GBP").upper()
    funding_instrument = funding.get("instruments") or {}
    target_instrument = target.get("instruments") or {}
    if not funding_instrument.get("id") or not target_instrument.get("id"):
        raise HTTPException(status_code=500, detail="Allocation instruments are unavailable.")

    funding_currency = str(funding_instrument.get("currency") or funding.get("currency") or base_currency).upper()
    if funding_currency != base_currency:
        raise HTTPException(status_code=400, detail="Allocation-date correction currently requires a base-currency funding instrument.")
    target_currency = str(target_instrument.get("currency") or target.get("currency") or base_currency).upper()
    target_fx = 1.0 if target_currency == base_currency else payload.target_fx_rate_to_base
    if target_fx is None:
        raise HTTPException(status_code=400, detail=f"target_fx_rate_to_base is required for {target_currency} corrected purchases.")

    reason = payload.reason.strip()
    decision_id = decision_id.lower()
    reversal_funding = {
        "portfolio_id": portfolio["id"], "instrument_id": funding_instrument["id"],
        "trade_date": funding["trade_date"], "side": "BUY",
        "quantity": float(funding.get("quantity") or 0.0), "price": float(funding.get("price") or 0.0),
        "fees": float(funding.get("fees") or 0.0), "currency": funding_currency,
        "fx_rate_to_base": float(funding.get("fx_rate_to_base") or 1.0),
        "note": f"ALLOCATION-REVERSAL {correction_id} ORIGINAL {decision_id} FUNDING: {reason}",
    }
    reversal_target = {
        "portfolio_id": portfolio["id"], "instrument_id": target_instrument["id"],
        "trade_date": target["trade_date"], "side": "SELL",
        "quantity": float(target.get("quantity") or 0.0), "price": float(target.get("price") or 0.0),
        "fees": float(target.get("fees") or 0.0), "currency": target_currency,
        "fx_rate_to_base": float(target.get("fx_rate_to_base") or 1.0),
        "note": f"ALLOCATION-REVERSAL {correction_id} ORIGINAL {decision_id} TARGET: {reason}",
    }
    corrected_funding = {
        "portfolio_id": portfolio["id"], "instrument_id": funding_instrument["id"],
        "trade_date": payload.corrected_trade_date.isoformat(), "side": "SELL",
        "quantity": payload.funding_quantity, "price": payload.funding_price,
        "fees": payload.funding_fees, "currency": funding_currency, "fx_rate_to_base": 1.0,
        "note": f"ALLOCATION {correction_id} FUNDING: [CORRECTS {decision_id}] {reason}",
    }
    corrected_target = {
        "portfolio_id": portfolio["id"], "instrument_id": target_instrument["id"],
        "trade_date": payload.corrected_trade_date.isoformat(), "side": "BUY",
        "quantity": payload.target_quantity, "price": payload.target_price,
        "fees": payload.target_fees, "currency": target_currency, "fx_rate_to_base": float(target_fx),
        "note": f"ALLOCATION {correction_id} TARGET: [CORRECTS {decision_id}] {reason}",
    }

    validation_rows = []
    for row, instrument in [
        (reversal_funding, funding_instrument), (reversal_target, target_instrument),
        (corrected_funding, funding_instrument), (corrected_target, target_instrument),
    ]:
        cloned = dict(row)
        cloned["instruments"] = instrument
        validation_rows.append(cloned)
    candidate = list(raw_transactions) + validation_rows
    validation = _validate_effective_ledger(portfolio, candidate)

    funding_proceeds = (payload.funding_quantity * payload.funding_price - payload.funding_fees)
    target_cost_base = (payload.target_quantity * payload.target_price + payload.target_fees) * float(target_fx)
    return {
        "correction_id": correction_id,
        "original_decision_id": decision_id,
        "original_trade_date": original_date.isoformat(),
        "corrected_trade_date": payload.corrected_trade_date.isoformat(),
        "funding_symbol": funding_instrument.get("symbol"),
        "target_symbol": target_instrument.get("symbol"),
        "funding_proceeds_base": funding_proceeds,
        "target_cost_base": target_cost_base,
        "net_cash_impact": funding_proceeds - target_cost_base,
        "rows": [reversal_funding, reversal_target, corrected_funding, corrected_target],
        "validation": {"ending_cash": validation["ending_cash"], "minimum_cash": validation["minimum_cash"], "base_currency": base_currency},
    }


''' + helper_marker
s = s.replace(helper_marker, helpers, 1)

# 3) Make validation/lookups use economic history after corrections.
s = s.replace('''    transactions = _transactions(portfolio["id"])\n    base_currency = str(portfolio.get("base_currency") or "GBP").upper()\n    book = _derive_book(transactions, base_currency)\n''', '''    transactions = _effective_transactions(_transactions(portfolio["id"]))\n    base_currency = str(portfolio.get("base_currency") or "GBP").upper()\n    book = _derive_book(transactions, base_currency)\n''', 1)
s = s.replace('''    transactions = _transactions(portfolio["id"])\n    fx = 1.0 if currency == base_currency else float(payload.fx_rate_to_base)\n''', '''    transactions = _effective_transactions(_transactions(portfolio["id"]))\n    fx = 1.0 if currency == base_currency else float(payload.fx_rate_to_base)\n''', 1)
s = s.replace('''    transactions = _transactions(portfolio["id"])\n\n    funding_symbol = payload.funding_symbol.strip().upper()\n''', '''    transactions = _effective_transactions(_transactions(portfolio["id"]))\n\n    funding_symbol = payload.funding_symbol.strip().upper()\n''', 1)

# 4) Add admin raw-ledger endpoint + paired preview/commit endpoints.
admin_marker = '''@router.get("/admin/{slug}/journal")\ndef get_admin_journal(slug: str, x_admin_secret: Optional[str] = Header(default=None, alias="X-Admin-Secret")) -> Dict[str, Any]:\n'''
if admin_marker not in s:
    raise SystemExit('admin journal marker missing')
admin_add = r'''@router.get("/admin/{slug}/transactions")
def get_admin_transactions(slug: str, x_admin_secret: Optional[str] = Header(default=None, alias="X-Admin-Secret")) -> Dict[str, Any]:
    _require_admin(x_admin_secret)
    portfolio = _portfolio(slug)
    raw = _transactions(portfolio["id"])
    return {"transactions": raw, "effective_transactions": _effective_transactions(raw)}


@router.post("/admin/{slug}/allocation/{decision_id}/correct/preview")
def preview_allocation_correction(
    slug: str,
    decision_id: str,
    payload: AdminAllocationCorrectionPayload,
    x_admin_secret: Optional[str] = Header(default=None, alias="X-Admin-Secret"),
) -> Dict[str, Any]:
    _require_admin(x_admin_secret)
    portfolio = _portfolio(slug)
    raw = _transactions(portfolio["id"])
    preview_id = uuid4().hex[:12]
    preview = _build_allocation_correction(portfolio, raw, decision_id, payload, preview_id)
    preview.pop("rows", None)
    preview["preview_only"] = True
    return preview


@router.post("/admin/{slug}/allocation/{decision_id}/correct")
def correct_allocation(
    slug: str,
    decision_id: str,
    payload: AdminAllocationCorrectionPayload,
    x_admin_secret: Optional[str] = Header(default=None, alias="X-Admin-Secret"),
) -> Dict[str, Any]:
    _require_admin(x_admin_secret)
    portfolio = _portfolio(slug)
    raw = _transactions(portfolio["id"])
    correction_id = uuid4().hex[:12]
    correction = _build_allocation_correction(portfolio, raw, decision_id, payload, correction_id)
    rows = _supabase_post_many("portfolio_transactions", correction["rows"])
    if len(rows) != 4:
        raise HTTPException(status_code=502, detail="Allocation correction did not return all four audit rows.")
    effective = _effective_transactions(_transactions(portfolio["id"]))
    corrected_target = next((txn for txn in effective if (_allocation_note(txn) or {}).get("decision_id") == correction_id and (_allocation_note(txn) or {}).get("leg") == "TARGET"), None)
    corrected_funding = next((txn for txn in effective if (_allocation_note(txn) or {}).get("decision_id") == correction_id and (_allocation_note(txn) or {}).get("leg") == "FUNDING"), None)
    return {
        "correction_id": correction_id,
        "original_decision_id": decision_id.lower(),
        "audit_rows": rows,
        "funding_transaction": corrected_funding,
        "target_transaction": corrected_target,
        "corrected_trade_date": payload.corrected_trade_date.isoformat(),
    }


''' + admin_marker
s = s.replace(admin_marker, admin_add, 1)

# 5) Public portfolio and performance use the economic projection; public transaction feed stays clean.
s = s.replace('''def get_portfolio(slug: str) -> Dict[str, Any]:\n    portfolio = _portfolio(slug)\n    transactions = _transactions(portfolio["id"])\n''', '''def get_portfolio(slug: str) -> Dict[str, Any]:\n    portfolio = _portfolio(slug)\n    transactions = _effective_transactions(_transactions(portfolio["id"]))\n''', 1)
s = s.replace('''def get_transactions(slug: str) -> Dict[str, Any]:\n    portfolio = _portfolio(slug)\n    return {"transactions": _transactions(portfolio["id"])}\n''', '''def get_transactions(slug: str) -> Dict[str, Any]:\n    portfolio = _portfolio(slug)\n    return {"transactions": _effective_transactions(_transactions(portfolio["id"]))}\n''', 1)
s = s.replace('''def get_performance(slug: str) -> Dict[str, Any]:\n    portfolio = _portfolio(slug)\n    transactions = _transactions(portfolio["id"])\n''', '''def get_performance(slug: str) -> Dict[str, Any]:\n    portfolio = _portfolio(slug)\n    transactions = _effective_transactions(_transactions(portfolio["id"]))\n''', 1)

p.write_text(s, encoding='utf-8')
print('allocation correction backend patched')
