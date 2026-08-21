import { describe, expect, it, vi } from "vitest";
import { CommandRegistry, eventShortcut, isTextEntryTarget, normalizeShortcut } from "./commandRegistry";

describe("CommandRegistry", () => {
  it("registers, publishes and removes page-scoped commands", () => {
    const registry = new CommandRegistry();
    const listener = vi.fn();
    registry.subscribe(listener);
    const command = { id: "shot.generate", label: "生成当前镜头", group: "当前页面" as const, shortcut: "G", run: vi.fn() };
    const dispose = registry.register(command);
    expect(registry.list()).toEqual([command]);
    expect(listener).toHaveBeenCalledTimes(1);
    dispose();
    expect(registry.list()).toEqual([]);
    expect(listener).toHaveBeenCalledTimes(2);
  });

  it("rejects duplicate ids so two pages cannot silently shadow an action", () => {
    const registry = new CommandRegistry();
    registry.register({ id: "shot.save", label: "保存", group: "当前页面", run: vi.fn() });
    expect(() => registry.register({ id: "shot.save", label: "另一个保存", group: "当前页面", run: vi.fn() })).toThrow(/already registered/);
  });

  it("normalizes shortcuts and protects text-entry and interactive targets", () => {
    const input = document.createElement("input");
    const button = document.createElement("button");
    expect(isTextEntryTarget(input)).toBe(true);
    expect(isTextEntryTarget(button)).toBe(true);
    expect(normalizeShortcut("Cmd+K")).toBe("meta+k");
    expect(eventShortcut(new KeyboardEvent("keydown", { key: "K", ctrlKey: true }))).toBe("ctrl+k");
  });
});
