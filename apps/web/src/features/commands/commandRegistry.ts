export type CommandContext = {
  projectId?: string;
  episodeId?: string;
  shotId?: string;
  navigate: (to: string) => void;
};

export type StudioCommand = {
  id: string;
  label: string;
  group: "创作" | "工具" | "导航" | "系统" | "当前页面";
  description?: string;
  keywords?: string[];
  shortcut?: string;
  enabled?: (context: CommandContext) => boolean;
  run: (context: CommandContext) => void | Promise<void>;
};

type Listener = () => void;

/**
 * Runtime command catalogue. Page features register commands while mounted;
 * unmounting removes them so shortcuts can never target stale shot state.
 */
export class CommandRegistry {
  private readonly commands = new Map<string, StudioCommand>();
  private readonly listeners = new Set<Listener>();
  private snapshot: StudioCommand[] = [];

  register(command: StudioCommand): () => void {
    if (this.commands.has(command.id)) throw new Error(`Command already registered: ${command.id}`);
    this.commands.set(command.id, command);
    this.snapshot = [...this.commands.values()];
    this.emit();
    return () => {
      if (this.commands.get(command.id) === command) {
        this.commands.delete(command.id);
        this.snapshot = [...this.commands.values()];
        this.emit();
      }
    };
  }

  list(): StudioCommand[] {
    return this.snapshot;
  }

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  private emit() {
    for (const listener of this.listeners) listener();
  }
}

export const studioCommandRegistry = new CommandRegistry();

export function isTextEntryTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return Boolean(target.closest("input, textarea, select, button, a, [contenteditable='true'], [role='button'], [role='dialog']"));
}

export function normalizeShortcut(shortcut: string): string {
  return shortcut.trim().toLowerCase().replace("cmd", "meta").replace("control", "ctrl");
}

export function eventShortcut(event: KeyboardEvent): string {
  const parts: string[] = [];
  if (event.ctrlKey) parts.push("ctrl");
  if (event.metaKey) parts.push("meta");
  if (event.altKey) parts.push("alt");
  if (event.shiftKey) parts.push("shift");
  const key = event.key.toLowerCase();
  if (!["control", "meta", "alt", "shift"].includes(key)) parts.push(key);
  return parts.join("+");
}
