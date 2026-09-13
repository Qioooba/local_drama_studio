import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AssetMentionInput, type AssetMentionReference } from "./AssetMentionInput";

const options = [
  { assetId: "asset-ning-0001", bindingId: "binding-1", stateId: "state-red", name: "阿宁", kind: "角色", status: "ACTIVE" },
  { assetId: "asset-ning-0002", bindingId: "binding-2", stateId: null, name: "阿宁", kind: "道具", status: "ACTIVE" },
];

describe("AssetMentionInput", () => {
  it("keeps same-name choices distinct by stable binding and asset IDs", () => {
    const change = vi.fn();
    render(<AssetMentionInput value="看向 @阿" references={[]} options={options} onChange={change} />);
    const choices = screen.getAllByRole("option");
    expect(choices).toHaveLength(2);
    fireEvent.click(choices[1]);
    expect(change).toHaveBeenCalledWith("看向 @阿宁 ", [expect.objectContaining({ assetId: "asset-ning-0002", bindingId: "binding-2" })]);
  });

  it("does not treat Enter during Chinese composition as a mention selection", () => {
    const change = vi.fn();
    render(<AssetMentionInput value="@阿" references={[]} options={options} onChange={change} />);
    const input = screen.getByLabelText("资产引用补充（可选）");
    fireEvent.compositionStart(input);
    fireEvent.keyDown(input, { key: "Enter" });
    expect(change).not.toHaveBeenCalled();
  });

  it("marks a stored reference expired without resolving pasted text into authority", () => {
    const reference = { ...options[0], stateId: "old-state", displayName: "阿宁" } as AssetMentionReference;
    render(<AssetMentionInput value="粘贴的 @阿宁" references={[reference]} options={options} onChange={vi.fn()} />);
    expect(screen.getByText(/已过期/)).toBeTruthy();
    expect(screen.getAllByRole("option")).toHaveLength(2);
  });
});
