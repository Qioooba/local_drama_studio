import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Director3DSpike } from "./Director3DSpike";
import type { Director3DValue } from "./types";

class ResizeObserverStub {
  observe() {}
  disconnect() {}
}

describe("Director3DSpike character authority", () => {
  beforeEach(() => {
    vi.stubGlobal("ResizeObserver", ResizeObserverStub);
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
  });
  afterEach(() => vi.restoreAllMocks());

  it("selects a shot-bound character asset instead of accepting a free-form name", () => {
    const onChange = vi.fn();
    render(<Director3DSpike participantOptions={[
      { id: "character-ning", label: "阿宁 · CHARACTER_ANING" },
      { id: "character-zhou", label: "周野 · CHARACTER_ZHOUYE" },
    ]} onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: "角色 A" }));
    expect(screen.queryByLabelText("角色名")).toBeNull();
    fireEvent.change(screen.getByLabelText("三维预演角色 A"), { target: { value: "character-ning" } });
    const next = onChange.mock.calls.at(-1)?.[0] as Director3DValue;
    expect(next.participants[0]).toMatchObject({ asset_id: "character-ning", label: "阿宁 · CHARACTER_ANING" });
  });
});
