# UI/UX Audit — 2026-08-12

Scope: real local React UI, blueprint 06/15/16, 1440×900, 1280×800 and 1024×768. API/state-machine/React Query/XYFlow behavior remains authoritative.

## P0

- Key screens are component-state navigation rather than recoverable routes; refresh/deep-link cannot restore view/shot/filter/scroll.
- Generation composer has no authoritative project/episode/shot/intent selector and therefore cannot safely create a real variant.
- Media input slot, prompt revision and preflight are presentation-only; submission must remain unavailable until wired to real APIs.
- Text-to-image has no matching published local Profile; the UI correctly blocks but needs a direct configuration workflow.
- System KPI cards consume the prime creative area on every page; move them to a compact top status strip outside overview/diagnostics.
- Global and project navigation are not yet separated according to blueprint 06.
- Existing review UI does not yet render thumbnail/media comparison or support keyboard completion of a structured review.
- Page state lacks region-level loading/error recovery and request IDs.

## P1

- `App.tsx` owns shell, queries and all feature panels; split by shell, production, generation, review, jobs, profiles and canvas.
- Typography includes 10px explanatory text; body/helper text must be at least 12–14px with verified contrast.
- Structural text arrows and lettermark should move to a consistent local SVG icon set.
- 1024px has no horizontal overflow, but the fixed 224px sidebar leaves a narrow production area; use collapsible navigation.
- Project selection silently defaults to the first project and first season/episode; selection needs visible controls and URL state.
- Candidate/variant controls need real selected/hover/focus/running/stale/approved semantics.
- Large episode, review and job lists need virtualization and fixed thumbnail dimensions.
- Favicon 404 adds console noise.

## P2

- Add optional dark media-review context while preserving neutral forms.
- Add command palette and documented keyboard shortcut map.
- Add density preference after core workflows are stable.
- Add a compact capacity dashboard after real benchmark data exists.

## Direction decision

1. Dark editing suite — cinematic, strongest for video, weaker for scripts/forms.
2. Warm production paper — calm and legible, weaker for media inspection.
3. **Hybrid professional workstation (selected)** — dark persistent chrome and media surfaces, neutral production surfaces, restrained vermilion creative accent.

The generic UI database classified the product as Video Streaming/OTT and proposed a pink video-first marketing hero. Its accessibility, density and interaction guidance was retained; its product pattern and palette were rejected because this is a production workstation.

## Browser evidence

- `output/playwright/ui-audit/audit-1440x900-thumb.jpg`
- `output/playwright/ui-audit/audit-1280x800-thumb.jpg`
- `output/playwright/ui-audit/audit-1024x768-thumb.jpg`
- No horizontal document overflow at 1024 (`scrollWidth == innerWidth == 1024`).
- One console error: missing `/favicon.ico`; tracked for immediate repair.

## 2026-08-15 re-audit

The original findings above are retained as the historical baseline. Current status was re-checked against the real production database and the generated React client.

### P0 disposition

- Resolved: view/project/episode/shot/review state is URL-backed, restored on refresh and synchronized on browser history navigation.
- Resolved: project, episode and shot selectors are visible in the shell/workbench; real FrameAnchor, keyframe candidate, immutable input and review flows are API-backed.
- Truthful blocker retained: text-to-image still has no matching Published local Profile. The UI routes to capability configuration and does not expose a fake submit path.
- Resolved: system KPIs are confined to overview; other views use a compact status strip.
- Resolved: navigation is separated into production and resource/system groups; the 1024px layout collapses to a horizontal navigation strip.
- Resolved: review uses fixed 320px small thumbnails, structured checks, independent selection/review actions and keyboard-operable native controls.
- Resolved: generated client errors now preserve structured code/status/retry guidance and show the API/body or response-header request ID. Active workspace queries render a regional alert with explicit retry; canvas and mutation errors remain local to their owning region.

### P1 disposition

- Resolved: every visible text sample on generation, review and canvas production routes computes to at least 12px; every enabled button/select/input/textarea computes to at least 40px high.
- Resolved: three production viewports have zero document-level horizontal overflow, console/page errors, failed responses and original-media requests.
- Resolved: project/episode selection is visible and canonicalized into the URL; it is no longer a silent in-memory default.
- Resolved: fixed thumbnail dimensions and bounded canvas/review projections prevent layout shifts on the production routes covered here.
- Resolved: favicon requests no longer produce browser errors.
- Partially resolved refactor: review inbox, jobs/capacity and shared progressive-list logic now live under `features/`; `App.tsx` still owns profiles, canvas, production and gate projections. Remaining work is maintainability debt, not an observed P0 workflow failure.
- Open refinement: replace remaining structural arrow/lettermark glyphs with the approved local SVG icon set.
- Partially resolved scale refinement: review and job panels render an initial 50-row window, preserve deep-linked selections outside that window, add explicit 50-row expansion and let the browser skip off-screen row layout/paint. Episode pagination remains a later refinement; unrestricted large-list UX is not claimed.

### Verification evidence

- `tests/e2e/g10_typography_accessibility.spec.ts` exercises 1440×900 generation, 1280×800 review and 1024×768 canvas using real production state.
- `docs/evidence/g10/typography-accessibility-uat-2026-08-15.json` records the computed typography/control floor, overflow, browser errors, failed responses and original-media request audit.
- Web unit suite: 17/17, including structured request-ID propagation and regional retry recovery.
- Production build and the combined G9/G10 three-viewport Playwright suite pass. No original-resolution image was loaded or captured for this re-audit.
