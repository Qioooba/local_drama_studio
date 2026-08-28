# Quick Create page override

This page is a global, standalone generation workspace. It is not nested under Projects and must not imply that a project will be created.

## Information architecture

- Header: page purpose plus a compact comparison between standalone generation and project production.
- Composer: one visible prompt field, an action-first recipe choice, and a single primary action.
- Action recipes: text-to-image, text-to-image-to-video, and text-to-video are equal first-class choices. A missing executable route never hides or disables the recipe; show the missing stage and its recovery path.
- Model catalog: group capability-specific execution routes by underlying model identity. A multi-capability model appears once in the catalog, carries explicit action labels, and is available in every compatible stage.
- Model stages: after the recipe is chosen, show only the stages it contains and only models supporting each stage action. Each stage exposes model-declared parameters under progressive disclosure.
- Presets: a stage-level preset combines one model with a parameter snapshot. Applying a preset keeps every field editable; saving and preset management stay beside the parameters they affect.
- Route settings: language, candidate count, and remote-outbound consent remain separate from model presets.
- Plan gate: show exact mode, dimensions, duration, FPS, profiles, and expanded prompts before submission.
- Execution: persistent progress, cancel/recovery actions, and truthful local runtime state.
- Result: inline poster/video preview, download action, provenance summary, and recent standalone runs.

## Interaction invariants

- Never ask for project, season, episode, or shot metadata.
- Never say that the output has been added to a project or material library.
- Keyframe selection means “use this artifact as video input,” not approval or promotion.
- Leaving the page does not cancel background generation; recent runs restore observation.
- Remote text transmission requires explicit consent. Images and videos remain local unless the selected capability states otherwise.
- A model selector chooses a model route compatible with the current action, not a preconfigured parameter bundle. User-facing model identity and immutable execution route are separate concepts.
- Publication/readiness is route state, not model classification. Never summarize a missing route as “no model”; explain which action route or workflow is not executable.
- Parameter changes affect the current run immediately and invalidate any stale execution plan.
- “Use model default” must be available per field; users never need to create or publish a new model Profile to change run parameters.
- Presets are conveniences, not authorities. The execution snapshot records the final explicit model IDs and parameters, whether or not a preset was used.
- Empty, planning, awaiting selection, running, failed, cancelled, and succeeded states each have an explicit recovery or next action.
