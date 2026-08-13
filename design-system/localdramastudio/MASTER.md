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

## 5. Generation workflow

One ordered path:

1. Select project/episode/shot and GenerationIntent.
2. Choose mode: text-to-image, image-to-video, text-to-video, reference-to-video, or capability-driven advanced mode.
3. Bind semantic inputs. Inputs must be registered MediaVersions; thumbnails load by default.
4. Choose an explicitly published CapabilityProfileVersion. Never silently fallback.
5. Configure prompt/camera/duration/seed using profile schema.
6. Run preflight showing jobs, runtime, GPU exclusivity, time/disk estimate and blockers.
7. Confirm exact action in a semantic dialog.
8. Compare candidates, review, select winner, then separately approve/promote.

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

