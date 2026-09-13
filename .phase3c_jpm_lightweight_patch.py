from pathlib import Path

path=Path('company_driver_tables.py')
text=path.read_text(encoding='utf-8')

# Lightweight raw-HTML helpers for very large bank filings.
text=text.replace('import re\nfrom datetime import date\n','import html as html_lib\nimport re\nfrom datetime import date\n',1)

anchor='''def _row_cells(row: Any) -> List[str]:\n    return [_clean(cell.get_text(" ", strip=True)) for cell in row.find_all(["th", "td"])]\n'''
assert anchor in text
insert=anchor+r'''


def _strip_html_fragment(fragment: Any) -> str:
    """Collapse a small HTML fragment without constructing a full filing DOM."""
    value=re.sub(r"(?is)<script\b.*?</script>|<style\b.*?</style>", " ", str(fragment or ""))
    value=re.sub(r"(?is)<[^>]+>", " ", value)
    return _clean(html_lib.unescape(value))


def _raw_row_cells(row_html: str) -> List[str]:
    cells=[]
    for match in re.finditer(r"(?is)<(?:td|th)\b[^>]*>(.*?)</(?:td|th)\s*>", str(row_html or "")):
        cells.append(_strip_html_fragment(match.group(1)))
    return cells
'''
text=text.replace(anchor,insert,1)

# Change only the JPM rule to the raw parser.
text=text.replace('''    ("JPM", "cet1_ratio"): {\n        "extractor": "jpm_cet1_ratio",''','''    ("JPM", "cet1_ratio"): {\n        "extractor": "jpm_cet1_ratio_raw",''',1)

# Insert the raw parser before the existing soup parser; keep the soup version for tests/backward compatibility.
marker='''def _jpm_cet1_ratio(soup: BeautifulSoup, filing: Dict[str, Any]) -> List[Dict[str, Any]]:\n'''
assert marker in text
raw=r'''
def _jpm_cet1_ratio_raw(html: str, filing: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract firm Standardized CET1 without parsing JPM's ~11 MB filing into a DOM.

    JPM includes a selected-metrics row labelled "Common equity Tier 1 (CET1)
    capital ratio - Standardized". We prefer that explicit row. A conservative
    fallback accepts a "CET1 capital ratio" row only when nearby table context
    explicitly contains "Standardized". The first numeric cell after the label is
    the current reporting-period Firm ratio.
    """
    end=_period_end(filing)
    if not end:
        return []
    source=str(html or "")
    candidates=[]
    for match in re.finditer(r"(?is)<tr\b[^>]*>.*?</tr\s*>", source):
        row_html=match.group(0)
        row_text=_strip_html_fragment(row_html).lower()
        explicit=("common equity tier 1" in row_text and "capital ratio" in row_text and "standardized" in row_text)
        fallback=("cet1 capital ratio" in row_text and "standardized" in _strip_html_fragment(source[max(0,match.start()-8000):match.start()]).lower())
        if not (explicit or fallback):
            continue
        cells=_raw_row_cells(row_html)
        label_index=next((i for i,cell in enumerate(cells) if ("common equity tier 1" in cell.lower() or "cet1 capital ratio" in cell.lower()) and "capital ratio" in cell.lower()),None)
        if label_index is None:
            continue
        nums=[value for value in (_number(cell) for cell in cells[label_index+1:]) if value is not None]
        if not nums:
            continue
        value=nums[0]
        if not (5.0 <= value <= 30.0):
            continue
        candidates.append((0 if explicit else 1,value))
    if not candidates:
        return []
    candidates.sort(key=lambda item:item[0])
    value=candidates[0][1]
    return [_base_observation(
        filing,
        period_type="instant",
        start=None,
        end=end,
        instant=end,
        value=value,
        unit_ref="percent",
        qualified_concept=None,
        dimensions=[],
        extraction_method="sec_filing_table_raw_row",
        source_label="Firm CET1 capital ratio - Standardized",
    )]


'''
text=text.replace(marker,raw+marker,1)

# Dispatch the JPM raw parser before constructing BeautifulSoup.
old='''    extractor = _EXTRACTORS[rule["extractor"]]\n    filings = company_driver_filings.recent_periodic_filings(\n'''
new='''    extractor_name = rule["extractor"]\n    extractor = _EXTRACTORS.get(extractor_name)\n    filings = company_driver_filings.recent_periodic_filings(\n'''
assert old in text
text=text.replace(old,new,1)
old='''        html = company_driver_filings._sec_get_text(filing["source_url"])\n        soup = BeautifulSoup(html, "html.parser")\n        extracted = extractor(soup, filing)\n'''
new='''        html = company_driver_filings._sec_get_text(filing["source_url"])\n        if extractor_name == "jpm_cet1_ratio_raw":\n            extracted = _jpm_cet1_ratio_raw(html, filing)\n        else:\n            if extractor is None:\n                raise ValueError(f"Unknown filing-table extractor: {extractor_name}")\n            soup = BeautifulSoup(html, "html.parser")\n            extracted = extractor(soup, filing)\n'''
assert old in text
text=text.replace(old,new,1)

path.write_text(text,encoding='utf-8')
print('PHASE3C_JPM_LIGHTWEIGHT_PATCHED')
