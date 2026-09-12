from pathlib import Path

path = Path('fundamentals_store.py')
text = path.read_text()

old_doc = '''def persist_snapshot(
    *,
    identity: Dict[str, Any],
    entity_name: Optional[str],
    normalized: Dict[str, Dict[str, Any]],
    schema_version: str,
    ttl_seconds: int = FUNDAMENTALS_TTL_SECONDS,
) -> Dict[str, Any]:
    """Stage a complete snapshot, atomically activate it, then purge older copies."""
'''
new_doc = '''def persist_snapshot(
    *,
    identity: Dict[str, Any],
    entity_name: Optional[str],
    normalized: Dict[str, Dict[str, Any]],
    schema_version: str,
    ttl_seconds: int = FUNDAMENTALS_TTL_SECONDS,
) -> Dict[str, Any]:
    """Stage a complete snapshot, activate it, then retire only the prior active copy."""
'''
if text.count(old_doc) != 1:
    raise SystemExit('persist_snapshot doc anchor mismatch')
text = text.replace(old_doc, new_doc, 1)

old_anchor = '''    if not ticker:
        raise FundamentalsStoreError("Ticker is required for fundamentals persistence.")

    sync_id = str(uuid4())
'''
new_anchor = '''    if not ticker:
        raise FundamentalsStoreError("Ticker is required for fundamentals persistence.")

    # Capture only the snapshot that was active before this refresh began. Cleanup
    # must never use a broad "all except mine" delete because a second Render worker
    # may have activated a newer snapshot while this worker was writing.
    previous_sync_id = None
    try:
        previous_state = get_sync_state(ticker, schema_version)
        if previous_state:
            previous_sync_id = previous_state.get("active_sync_id")
    except FundamentalsStoreError:
        previous_sync_id = None

    sync_id = str(uuid4())
'''
if text.count(old_anchor) != 1:
    raise SystemExit('ticker anchor mismatch')
text = text.replace(old_anchor, new_anchor, 1)

old_cleanup = '''    # Cleanup happens only after activation. Failure here is harmless because reads are
    # always pinned to active_sync_id and will never expose an older/staged snapshot.
    try:
        _request(
            "DELETE",
            OBSERVATIONS_TABLE,
            params={
                "ticker": f"eq.{ticker}",
                "schema_version": f"eq.{schema_version}",
                "sync_id": f"neq.{sync_id}",
            },
            prefer="return=minimal",
        )
    except FundamentalsStoreError:
        pass
'''
new_cleanup = '''    # Retire only the snapshot observed before staging. If another worker activates a
    # newer snapshot concurrently, this worker can never delete that newer snapshot.
    # A losing staged snapshot may remain orphaned, which is safe and can be cleaned
    # independently without affecting reads.
    if previous_sync_id and str(previous_sync_id) != sync_id:
        _delete_staged_sync(str(previous_sync_id))
'''
if text.count(old_cleanup) != 1:
    raise SystemExit('cleanup anchor mismatch')
text = text.replace(old_cleanup, new_cleanup, 1)

path.write_text(text)
