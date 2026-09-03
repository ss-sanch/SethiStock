from pathlib import Path

p = Path('sethiportfolio.py')
s = p.read_text(encoding='utf-8')
old = '''    reason = payload.reason.strip()\n    decision_id = decision_id.lower()\n    reversal_funding = {\n'''
new = '''    reason = payload.reason.strip()\n    funding_meta = _allocation_note(funding) or {}\n    target_meta = _allocation_note(target) or {}\n    funding_public_reason = str(funding_meta.get("reason") or "").strip() or reason\n    target_public_reason = str(target_meta.get("reason") or "").strip() or reason\n    decision_id = decision_id.lower()\n    reversal_funding = {\n'''
if old not in s:
    raise SystemExit('reason insertion marker missing')
s = s.replace(old, new, 1)
old = '''        "note": f"ALLOCATION {correction_id} FUNDING: [CORRECTS {decision_id}] {reason}",\n'''
new = '''        "note": f"ALLOCATION {correction_id} FUNDING: [CORRECTS {decision_id}] {funding_public_reason}",\n'''
if old not in s:
    raise SystemExit('corrected funding note marker missing')
s = s.replace(old, new, 1)
old = '''        "note": f"ALLOCATION {correction_id} TARGET: [CORRECTS {decision_id}] {reason}",\n'''
new = '''        "note": f"ALLOCATION {correction_id} TARGET: [CORRECTS {decision_id}] {target_public_reason}",\n'''
if old not in s:
    raise SystemExit('corrected target note marker missing')
s = s.replace(old, new, 1)
old = '''        "net_cash_impact": funding_proceeds - target_cost_base,\n        "rows": [reversal_funding, reversal_target, corrected_funding, corrected_target],\n'''
new = '''        "net_cash_impact": funding_proceeds - target_cost_base,\n        "public_reason": target_public_reason,\n        "rows": [reversal_funding, reversal_target, corrected_funding, corrected_target],\n'''
if old not in s:
    raise SystemExit('preview response marker missing')
s = s.replace(old, new, 1)
p.write_text(s, encoding='utf-8')
print('public allocation rationale preserved')
