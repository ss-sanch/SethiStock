from pathlib import Path

# Trigger patch workflow after workflow definition exists on the branch.
p = Path('sethiportfolio.py')
s = p.read_text(encoding='utf-8')

old = '''class AdminActiveAllocationPayload(BaseModel):\n    trade_date: date\n    funding_symbol: str = Field(min_length=1, max_length=32)\n    funding_quantity: float = Field(gt=0)\n    funding_price: float = Field(ge=0)\n    funding_fees: float = Field(default=0, ge=0)\n    target_symbol: str = Field(min_length=1, max_length=32)\n'''
new = '''class AdminActiveAllocationPayload(BaseModel):\n    trade_date: date\n    funding_symbol: str = Field(min_length=1, max_length=32)\n    funding_quantity: float = Field(gt=0)\n    funding_price: float = Field(ge=0)\n    funding_fees: float = Field(default=0, ge=0)\n    funding_fx_rate_to_base: Optional[float] = Field(default=None, gt=0)\n    target_symbol: str = Field(min_length=1, max_length=32)\n'''
if old not in s: raise SystemExit('active allocation payload marker missing')
s = s.replace(old, new, 1)

old = '''class AdminAllocationCorrectionPayload(BaseModel):\n    corrected_trade_date: date\n    funding_quantity: float = Field(gt=0)\n    funding_price: float = Field(ge=0)\n    funding_fees: float = Field(default=0, ge=0)\n    target_quantity: float = Field(gt=0)\n'''
new = '''class AdminAllocationCorrectionPayload(BaseModel):\n    corrected_trade_date: date\n    funding_quantity: float = Field(gt=0)\n    funding_price: float = Field(ge=0)\n    funding_fees: float = Field(default=0, ge=0)\n    funding_fx_rate_to_base: Optional[float] = Field(default=None, gt=0)\n    target_quantity: float = Field(gt=0)\n'''
if old not in s: raise SystemExit('allocation correction payload marker missing')
s = s.replace(old, new, 1)

old = '''    funding_currency = str(funding_instrument.get("currency") or funding.get("currency") or base_currency).upper()\n    if funding_currency != base_currency:\n        raise HTTPException(status_code=400, detail="Allocation-date correction currently requires a base-currency funding instrument.")\n    target_currency = str(target_instrument.get("currency") or target.get("currency") or base_currency).upper()\n'''
new = '''    funding_currency = str(funding_instrument.get("currency") or funding.get("currency") or base_currency).upper()\n    funding_fx = 1.0 if funding_currency == base_currency else payload.funding_fx_rate_to_base\n    if funding_fx is None:\n        raise HTTPException(status_code=400, detail=f"funding_fx_rate_to_base is required for {funding_currency} corrected sales in a {base_currency} portfolio.")\n    target_currency = str(target_instrument.get("currency") or target.get("currency") or base_currency).upper()\n'''
if old not in s: raise SystemExit('correction funding restriction marker missing')
s = s.replace(old, new, 1)

old = '''        "quantity": payload.funding_quantity, "price": payload.funding_price,\n        "fees": payload.funding_fees, "currency": funding_currency, "fx_rate_to_base": 1.0,\n'''
new = '''        "quantity": payload.funding_quantity, "price": payload.funding_price,\n        "fees": payload.funding_fees, "currency": funding_currency, "fx_rate_to_base": float(funding_fx),\n'''
if old not in s: raise SystemExit('corrected funding FX marker missing')
s = s.replace(old, new, 1)

old = '''    funding_proceeds = (payload.funding_quantity * payload.funding_price - payload.funding_fees)\n    target_cost_base = (payload.target_quantity * payload.target_price + payload.target_fees) * float(target_fx)\n'''
new = '''    funding_proceeds = (payload.funding_quantity * payload.funding_price - payload.funding_fees) * float(funding_fx)\n    target_cost_base = (payload.target_quantity * payload.target_price + payload.target_fees) * float(target_fx)\n'''
if old not in s: raise SystemExit('correction funding proceeds marker missing')
s = s.replace(old, new, 1)

old = '''    funding_currency = str(funding_instrument.get("currency") or base_currency).upper()\n    funding_fx = 1.0 if funding_currency == base_currency else None\n    if funding_fx is None:\n        raise HTTPException(status_code=400, detail=f"Funding instrument {funding_symbol} is not base-currency denominated; active-allocation funding FX is not yet supported.")\n\n    target_currency = payload.target_currency.strip().upper()\n'''
new = '''    funding_currency = str(funding_instrument.get("currency") or base_currency).upper()\n    funding_fx = 1.0 if funding_currency == base_currency else payload.funding_fx_rate_to_base\n    if funding_fx is None:\n        raise HTTPException(status_code=400, detail=f"funding_fx_rate_to_base is required for {funding_currency} sales in a {base_currency} portfolio.")\n\n    target_currency = payload.target_currency.strip().upper()\n'''
if old not in s: raise SystemExit('active allocation funding restriction marker missing')
s = s.replace(old, new, 1)

old = '''    return {\n        "decision_id": decision_id,\n        "funding_transaction": rows[0],\n        "target_transaction": rows[1],\n        "base_currency": base_currency,\n    }\n'''
new = '''    return {\n        "decision_id": decision_id,\n        "funding_transaction": rows[0],\n        "target_transaction": rows[1],\n        "funding_currency": funding_currency,\n        "funding_fx_rate_to_base": float(funding_fx),\n        "target_currency": target_currency,\n        "target_fx_rate_to_base": float(target_fx),\n        "base_currency": base_currency,\n    }\n'''
if old not in s: raise SystemExit('active allocation response marker missing')
s = s.replace(old, new, 1)

p.write_text(s, encoding='utf-8')
