import { useEffect } from "react";
import type { StudioCommand } from "./commandRegistry";
import { studioCommandRegistry } from "./commandRegistry";

/** Register a page-scoped action; memoize the command in the caller. */
export function useStudioCommand(command: StudioCommand | null) {
  useEffect(() => command ? studioCommandRegistry.register(command) : undefined, [command]);
}
