import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { BreadcrumbSeparatorIcon, GateStatusIcon, StatusDotIcon, StudioMarkIcon } from "./icons";

describe("local outline icon system", () => {
  it("keeps decorative SVGs out of the accessibility tree", () => {
    const { container } = render(<><StudioMarkIcon /><StatusDotIcon /><BreadcrumbSeparatorIcon /><GateStatusIcon passed /></>);
    const icons = [...container.querySelectorAll("svg")];
    expect(icons).toHaveLength(4);
    expect(icons.every((icon) => icon.getAttribute("aria-hidden") === "true")).toBe(true);
    expect(icons.every((icon) => icon.getAttribute("focusable") === "false")).toBe(true);
    expect(container.textContent).toBe("");
  });

  it("uses shape and check geometry for both gate states without text glyphs", () => {
    const { container, rerender } = render(<GateStatusIcon passed={false} />);
    expect(container.querySelectorAll("circle")).toHaveLength(1);
    expect(container.querySelectorAll("path")).toHaveLength(0);
    rerender(<GateStatusIcon passed />);
    expect(container.querySelectorAll("circle")).toHaveLength(1);
    expect(container.querySelectorAll("path")).toHaveLength(1);
  });
});
