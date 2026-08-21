import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import type { CommandContext, StudioCommand } from "./commandRegistry";
import { eventShortcut, isTextEntryTarget, normalizeShortcut, studioCommandRegistry } from "./commandRegistry";
import { SearchResultContent } from "../search-v2/SearchResultContent";
import { useNavigableSearch, type NavigableSearchResult } from "../search-v2/searchNavigation";
import "./command-palette.css";

const EMPTY: StudioCommand[] = [];

function score(command: StudioCommand, query: string): number {
  const needle = query.trim().toLocaleLowerCase();
  if (!needle) return 1;
  const label = command.label.toLocaleLowerCase();
  if (label === needle) return 100;
  if (label.startsWith(needle)) return 70;
  if (label.includes(needle)) return 45;
  const haystack = [command.group, command.description, ...(command.keywords ?? [])].filter(Boolean).join(" ").toLocaleLowerCase();
  return haystack.includes(needle) ? 20 : 0;
}

function displayShortcut(shortcut?: string) {
  if (!shortcut) return null;
  return shortcut.replace("Meta", "⌘").replace("Ctrl", "Ctrl").replaceAll("+", " + ");
}

export function CommandPalette({ context, baseCommands = EMPTY }: { context: CommandContext; baseCommands?: StudioCommand[] }) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const registered = useSyncExternalStore(
    (listener) => studioCommandRegistry.subscribe(listener),
    () => studioCommandRegistry.list(),
    () => EMPTY,
  );
  const available = useMemo(
    () => [...baseCommands, ...registered]
      .filter((command) => command.enabled?.(context) !== false)
      .map((command) => ({ command, score: score(command, query) }))
      .filter((item) => item.score > 0)
      .sort((a, b) => b.score - a.score || a.command.label.localeCompare(b.command.label, "zh-CN"))
      .map((item) => item.command),
    [baseCommands, registered, context, query],
  );
  const entitySearch = useNavigableSearch(query, context.projectId);
  const options = useMemo<Array<{ kind: "command"; command: StudioCommand } | { kind: "entity"; result: NavigableSearchResult }>>(
    () => [
      ...available.map((command) => ({ kind: "command" as const, command })),
      ...entitySearch.items.map((result) => ({ kind: "entity" as const, result })),
    ],
    [available, entitySearch.items],
  );

  const close = () => {
    setOpen(false);
    setQuery("");
    setActiveIndex(0);
  };

  const execute = async (command: StudioCommand) => {
    close();
    await command.run(context);
  };

  const executeOption = async (option: (typeof options)[number]) => {
    if (option.kind === "command") return execute(option.command);
    const route = option.result.route;
    close();
    context.navigate(route);
  };

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      dialog.showModal();
      requestAnimationFrame(() => searchRef.current?.focus());
    } else if (!open && dialog.open) dialog.close();
  }, [open]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const shortcut = eventShortcut(event);
      if (shortcut === "ctrl+k" || shortcut === "meta+k") {
        event.preventDefault();
        setOpen((value) => !value);
        return;
      }
      if (open || isTextEntryTarget(event.target) || event.repeat || event.ctrlKey || event.metaKey || event.altKey) return;
      const command = [...baseCommands, ...registered].find((candidate) => {
        if (!candidate.shortcut || candidate.enabled?.(context) === false) return false;
        return normalizeShortcut(candidate.shortcut) === shortcut;
      });
      if (command) {
        event.preventDefault();
        void command.run(context);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [baseCommands, context, open, registered]);

  useEffect(() => setActiveIndex(0), [query]);
  useEffect(() => {
    if (activeIndex >= options.length) setActiveIndex(Math.max(0, options.length - 1));
  }, [activeIndex, options.length]);

  return <>
    <button type="button" className="command-trigger" aria-label="打开命令面板" aria-keyshortcuts="Control+K Meta+K" onClick={() => setOpen(true)}>
      <svg viewBox="0 0 20 20" aria-hidden="true" focusable="false"><circle cx="8.5" cy="8.5" r="5.5"/><path d="m12.5 12.5 4 4"/></svg>
      <span>搜索命令与实体</span><kbd>Ctrl K</kbd>
    </button>
    <dialog ref={dialogRef} className="command-dialog" aria-labelledby="command-palette-title" onCancel={(event) => { event.preventDefault(); close(); }} onClose={() => setOpen(false)} onClick={(event) => { if (event.target === dialogRef.current) close(); }}>
      <div className="command-search-row">
        <svg viewBox="0 0 20 20" aria-hidden="true" focusable="false"><circle cx="8.5" cy="8.5" r="5.5"/><path d="m12.5 12.5 4 4"/></svg>
        <label htmlFor="studio-command-search" id="command-palette-title">查找页面或操作</label>
        <input
          ref={searchRef}
          id="studio-command-search"
          role="combobox"
          aria-autocomplete="list"
          aria-expanded="true"
          aria-controls="studio-command-results"
          aria-activedescendant={options[activeIndex] ? `command-option-${activeIndex}` : undefined}
          value={query}
          autoComplete="off"
          placeholder="搜索命令或创作实体，例如“EP03 S12”"
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "ArrowDown") { event.preventDefault(); setActiveIndex((index) => Math.min(index + 1, options.length - 1)); }
            if (event.key === "ArrowUp") { event.preventDefault(); setActiveIndex((index) => Math.max(index - 1, 0)); }
            if (event.key === "Enter" && options[activeIndex]) { event.preventDefault(); void executeOption(options[activeIndex]); }
          }}
        />
        <button type="button" className="command-close" aria-label="关闭命令面板" onClick={close}>Esc</button>
      </div>
      <div id="studio-command-results" className="command-results" role="listbox" aria-label="命令与搜索结果" aria-busy={entitySearch.status === "loading"}>
        {available.length > 0 && <div className="command-group-label" role="presentation">命令</div>}
        {available.map((command, index) => <button
          type="button"
          role="option"
          aria-selected={index === activeIndex}
          id={`command-option-${index}`}
          className={`command-result${index === activeIndex ? " active" : ""}`}
          key={command.id}
          onMouseMove={() => setActiveIndex(index)}
          onClick={() => void executeOption({ kind: "command", command })}
        >
          <span><strong>{command.label}</strong>{command.description && <small>{command.description}</small>}</span>
          <span className="command-meta"><em>{command.group}</em>{command.shortcut && <kbd>{displayShortcut(command.shortcut)}</kbd>}</span>
        </button>)}
        {entitySearch.items.length > 0 && <div className="command-group-label" role="presentation">创作实体</div>}
        {entitySearch.items.map((result, entityIndex) => {
          const index = available.length + entityIndex;
          return <button type="button" role="option" aria-selected={index === activeIndex} id={`command-option-${index}`} className={`command-result command-entity-result${index === activeIndex ? " active" : ""}`} key={`${result.subject_type}:${result.subject_id}`} onMouseMove={() => setActiveIndex(index)} onClick={() => void executeOption({ kind: "entity", result })}>
            <SearchResultContent item={result} />
          </button>;
        })}
        {entitySearch.status === "loading" && <div className="command-search-status" role="status">正在搜索创作实体…</div>}
        {entitySearch.status === "error" && <div className="command-search-status command-search-error" role="alert"><strong>实体搜索失败</strong><span>{entitySearch.error}。可修改关键词后重试。</span></div>}
        {query.trim().length >= 2 && entitySearch.status === "success" && entitySearch.items.length === 0 && <div className="command-search-status" role="status">没有匹配的创作实体。</div>}
        {options.length === 0 && entitySearch.status !== "loading" && entitySearch.status !== "error" && <div className="command-empty"><strong>没有匹配结果</strong><span>换个关键词，或按 Esc 返回当前工作。</span></div>}
      </div>
      <footer className="command-help"><span><kbd>↑</kbd><kbd>↓</kbd> 选择</span><span><kbd>Enter</kbd> 执行</span><span><kbd>Esc</kbd> 关闭</span></footer>
    </dialog>
  </>;
}
