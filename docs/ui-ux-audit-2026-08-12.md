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

