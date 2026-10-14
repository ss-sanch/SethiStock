# Runtime recovery notes

## 2026-09-13

The production Render service returned HTTP 502 for the root health endpoint and every SethiStock API route during a live diagnostic. A clean redeploy was triggered after Phase 3E frontend work was changed to lazy-load Company Drivers so expensive filing-history requests are not launched invisibly after every stock search.

## 2026-09-14

The production Render service again returned HTTP 502 across quote, chart, research, SEC fundamentals and telemetry routes after a frontend experiment launched several expensive requests concurrently at ticker-analysis start. The frontend was changed back to staged hydration (main stock response first, then research, then a lightweight EBITDA-only request, then the long-run SEC history). This operational-only commit triggers a clean Render redeploy after removing that request burst.

This file is operational documentation only and is not imported by the application.

## 2026-10-14

Production events repeatedly report `oomKilled` at the free service's 512 MiB limit. The live deployment is `0a70d60`; the later snapshot deployments failed. Compiling the current repository reproduces an `IndentationError` because `snapshot_hit` and `snapshot_ms` were placed outside the telemetry payload dictionary. Move the closing brace after those fields and add a syntax workflow plus an application-import regression check.

Bound the SEC facts cache to an estimated 32 MiB / 8 entries, parsed filing cache to 16 MiB / 8 entries and research cache to 2 MiB / 64 entries. Expire idle keys on access/write and skip caching individual oversized payloads. Serialise filing parsing and explicitly decompose BeautifulSoup trees after extraction. Replace per-request source executors with one three-worker pool whose slots remain occupied until timed-out sources really finish. Add a three-second timeout to the autocomplete provider call.

These changes reduce identified memory growth paths; they do not guarantee a 512 MiB RSS ceiling. After merging and deploying, inspect runtime cache statistics and Render memory/failure events during repeated ticker searches. A platform-generated restart response can lack CORS headers even when the application CORS middleware is configured correctly. The frontend also needs its stale autocomplete hostname corrected in `sethiway-main`.
