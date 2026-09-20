alter table public.sethistock_load_telemetry
    add column if not exists server_wake_ms integer check (server_wake_ms is null or server_wake_ms >= 0),
    add column if not exists server_uptime_s integer check (server_uptime_s is null or server_uptime_s >= 0),
    add column if not exists server_state text check (server_state is null or server_state in ('warm','cold','unknown'));
