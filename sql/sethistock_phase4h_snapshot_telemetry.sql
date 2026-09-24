alter table public.sethistock_load_telemetry
    add column if not exists snapshot_hit boolean,
    add column if not exists snapshot_ms integer check (snapshot_ms is null or snapshot_ms >= 0);
