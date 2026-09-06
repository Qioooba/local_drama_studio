# LocalDramaStudio Design System

> Status: ACTIVE. This file is the frontend visual and interaction source of truth. Page overrides live in `pages/`.

## 1. Product character

LocalDramaStudio is a professional, long-session desktop production workstation. It is not a streaming product, marketing page, generic AI chat app, or cloud billing console.

Design goals: restrained cinematic character, dense but readable production data, clear human gates, truthful local capability status, fast keyboard-first operation, and low-bandwidth media browsing.

## 2. Chosen direction

Use **Hybrid Professional Workstation**:

- Dark persistent chrome for navigation, machine state, timeline and media comparison.
- Low-glare warm-neutral work surfaces for forms, shot tables and review checklists.
- Vermilion accent only for the current creative action; semantic colors are reserved for status.
- No decorative gradients, glow, glassmorphism, giant marketing hero, or card-on-card nesting.

Rejected alternatives:

1. Full dark editing suite: excellent for video comparison, tiring for script/forms and dense metadata.
2. Full warm production paper: comfortable for writing, weak for media review and machine monitoring.
3. Pink OTT/video-first recommendation from the generic database: wrong product category and unsuitable for a production workstation.

## 3. Tokens

### Color

| Token | Value | Use |
|---|---:|---|
| `--chrome` | `#1F2625` | persistent navigation and top bar |
| `--canvas` | `#F4F1EB` | application work area |
| `--surface` | `#FFFDF8` | primary surface |
| `--surface-muted` | `#EBE7DE` | grouped controls and inactive steps |
| `--text` | `#20242B` | primary text |
| `--text-muted` | `#666B66` | secondary text, minimum 4.5:1 where body-sized |
| `--border` | `#D8D2C7` | separators and field borders |
| `--creative` | `#E9633B` | one primary creative action per screen |
| `--info` | `#316A9D` | available/information |
| `--running` | `#7457B5` | running progress, always paired with text |
| `--attention` | `#A96016` | human review/warning |
| `--selected` | `#2D7467` | selected, never labelled approved |
| `--approved` | `#32744D` | human-approved/verified only |
| `--danger` | `#B33A32` | failed/rejected/destructive |

Media viewers, multi-video comparison and timeline use `#111715` surfaces with `#F4F5F2` foreground.

### Type

- UI/body: local system stack `Inter, Segoe UI, Microsoft YaHei, sans-serif`; no external font request.
- Editorial headings only: `Georgia, Songti SC, serif`.
- Scale: 12 metadata, 13 compact body, 14 standard body, 16 emphasized body, 18 section title, 24 page title, 32 exceptional overview title.
- Body line height 1.5–1.65. Use tabular numerals for duration, seed, queue and resource values.

### Space and shape

- 4px base grid: 4 / 8 / 12 / 16 / 24 / 32 / 48.
- Controls minimum 40px desktop height; icon-only hit target minimum 44px.
- Radius: 6 controls, 8 fields, 10 panels, 12 exceptional containers. Pills only for short statuses.
- Shadows are rare: level 1 `0 1px 2px rgba(32,36,43,.06)`, level 2 for popovers only.

## 4. Application frame

- Top bar: 64–72px; project → season → episode → shot breadcrumb, LOCAL_ONLY, queue/GPU/disk.
- Global sidebar: 216–224px at >=1180; collapses at 1024, never competes with project navigation.
- Project secondary navigation follows production order: Overview → Script → Storyboard → Assets → Episode production → Images → Video → Audio/Subtitles → Timeline → Review → Delivery.
- The same episode keeps `view`, `shot`, filters, selection and scroll in URL/session state.
- Operational pages (profiles, jobs, diagnostics) remain separate from creative production pages.

## 5. Generation workflows

The product has two intentionally separate paths:

### Quick generation

1. Start from the global Quick Create entry, without choosing or creating a project.
2. Describe one visual result, then choose one action recipe: text-to-image, text-to-image-to-video, or text-to-video. Recipes remain selectable even when a required executable route is not ready; readiness is explained inside the selected recipe.
3. Choose a model for each active stage: text planning, text-to-image, text-to-video, or image-to-video. The selector contains models supporting that stage's action, not a frozen preset or an unrelated capability Profile.
4. Treat a model as a reusable identity with a set of supported actions. One video model may expose both text-to-video and image-to-video execution routes and must appear in both relevant stage selectors without being duplicated as two user-facing models.
5. Edit this run's model-declared parameters inline. Parameters are validated by the selected model schema and frozen into the job execution snapshot.
6. Optionally apply, save, update, favorite, or delete a reusable preset. A preset is only a named model-and-parameter starting point; applying it never locks the fields.
7. Review remote-outbound consent when applicable and inspect the exact effective parameters in the execution plan.
8. Submit global `QUICK_GENERATION` jobs for the active stages.
9. For image-producing recipes, compare independent output artifacts and select the final image or the image-to-video input.
10. Preview or download the resulting standalone image or video. It does not appear in project production records.

### Project production

The normal episode path is an Agent-orchestrated **Episode creation** workspace:

1. Inherit story authority plus project-level visual, output and model settings.
2. Present five creator-facing stages: episode plan and new assets, key imagery, video, audio/subtitles, compose/QC.
3. Ask only for quality, checkpoint strategy and whether dialogue audio is included.
4. Run the canonical production preflight and durable workflow. Stop on the chosen checkpoint or any exception.
5. Show only the stage summary and items that require attention. Route exceptional shots to the Shot Studio.
6. Keep resolution, aspect ratio, frame rate and model routing in project settings instead of repeating them per episode.

Shot Studio remains the advanced and exception path. There, a creator may select a shot and GenerationIntent, bind registered MediaVersions, choose an explicitly published CapabilityProfileVersion, configure profile-declared parameters, compare candidates, select a working version and separately approve it. Never silently fallback or collapse selection into approval.

Quick generation must never synthesize placeholder projects, episodes, shots, GenerationIntents, MediaVersions, or review records. Converting a standalone artifact into project material, if added later, must be an explicit user action with its own provenance event.

Retry is operational and stays in Job details. Resample/replay/prompt/source/first-last are creative variants and must never be merged into retry.

## 6. Component rules

- One primary CTA per screen/inspector. Disabled actions must retain an adjacent reason or enabled recovery link.
- Status uses text + icon/shape + color. `selected` is teal; `approved` is green.
- Async views preserve existing data during refresh; first load uses fixed-size skeletons.
- Error appears near the failing region with cause, request ID, retry and diagnostics link.
- Complex options use progressive disclosure; form labels are always visible.
- Lists over 50 items virtualize. Images use fixed aspect ratio, WebP thumbnail and lazy loading. Video loads poster first and proxy only on action.
- Use one SVG outline icon family at 16/20px with 1.75px stroke; no emoji or text arrows as structural icons.
- Motion is 150–220ms, transform/opacity only, and disabled by `prefers-reduced-motion`.

## 7. Responsive desktop

- 1440+: full sidebar, 12-column work area, composer + inspector.
- 1280: compact gutters and metadata; preserve composer + inspector.
- 1024: collapse global sidebar, stack inspector below composer, preserve all primary actions and no horizontal page scroll.
- Mobile is not a full editing target; monitoring remains readable, but complex review is desktop-only.

## 8. Accessibility and QA

- WCAG AA target: 4.5:1 normal text, 3:1 large text and graphical controls.
- Visible 3px focus ring; DOM order follows visual order; route changes focus the page heading.
- Keyboard can complete image review and proxy winner selection. Inputs disable global shortcuts while focused.
- Do not use color alone. All icon buttons have accessible names.
- Validate normal/loading/empty/error/blocked/running/success at 1440×900, 1280×800 and 1024×768.
- Every browser QA capture delivered to the user is a <=720px, quality 50–60 thumbnail; original evidence remains local.
