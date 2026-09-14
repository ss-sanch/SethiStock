# Runtime recovery notes

## 2026-09-13

The production Render service returned HTTP 502 for the root health endpoint and every SethiStock API route during a live diagnostic. A clean redeploy was triggered after Phase 3E frontend work was changed to lazy-load Company Drivers so expensive filing-history requests are not launched invisibly after every stock search.

## 2026-09-14

The production Render service again returned HTTP 502 across quote, chart, research, SEC fundamentals and telemetry routes after a frontend experiment launched several expensive requests concurrently at ticker-analysis start. The frontend was changed back to staged hydration (main stock response first, then research, then a lightweight EBITDA-only request, then the long-run SEC history). This operational-only commit triggers a clean Render redeploy after removing that request burst.

This file is operational documentation only and is not imported by the application.
