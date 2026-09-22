import { fireEvent, render, screen, cleanup } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Dialog, Drawer } from "./primitives";

/**
 * Regression coverage for FE-05 (a parent re-render must not steal input focus
 * from a controlled field inside Dialog/Drawer) and FE-06 (nested overlays must
 * share one Escape/Tab/focus-restoration owner: only the topmost layer reacts).
 *
 * The auditor's reproductions asserted the buggy behaviour; these tests assert
 * the fixed behaviour instead.
 */

function InlineCloseFieldHarness({ kind }: { kind: "dialog" | "drawer" }) {
  const [open, setOpen] = useState(true);
  const [note, setNote] = useState("");
  const body = (
    <textarea
      aria-label="批准说明"
      value={note}
      onChange={(event) => setNote(event.target.value)}
    />
  );
  // Inline `onClose` identity changes on every keystroke: exactly the call site
  // shape that used to re-run the focus effect of the primitive.
  return kind === "dialog" ? (
    <Dialog open={open} title="身份包批准" onClose={() => setOpen(false)}>
      {body}
    </Dialog>
  ) : (
    <Drawer open={open} title="参考图片" onClose={() => setOpen(false)}>
      {body}
    </Drawer>
  );
}

function NestedOverlayHarness({ inner }: { inner: "dialog" | "drawer" }) {
  const [outerOpen, setOuterOpen] = useState(false);
  const [innerOpen, setInnerOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOuterOpen(true)}>打开外层</button>
      <Dialog open={outerOpen} title="外层工作区" onClose={() => setOuterOpen(false)}>
        <button type="button" onClick={() => setInnerOpen(true)}>打开内层</button>
      </Dialog>
      {inner === "dialog" ? (
        <Dialog
          open={innerOpen}
          title="内层确认"
          onClose={() => setInnerOpen(false)}
          footer={<button type="button" onClick={() => setInnerOpen(false)}>确认关闭</button>}
        >
          <p>内层正文</p>
        </Dialog>
      ) : (
        <Drawer
          open={innerOpen}
          title="内层确认"
          onClose={() => setInnerOpen(false)}
          footer={<button type="button" onClick={() => setInnerOpen(false)}>确认关闭</button>}
        >
          <p>内层正文</p>
        </Drawer>
      )}
    </>
  );
}

function NestedDrawerOuterHarness() {
  const [outerOpen, setOuterOpen] = useState(false);
  const [innerOpen, setInnerOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOuterOpen(true)}>打开外层抽屉</button>
      <Drawer open={outerOpen} title="外层抽屉" onClose={() => setOuterOpen(false)}>
        <button type="button" onClick={() => setInnerOpen(true)}>打开内层确认</button>
      </Drawer>
      <Dialog open={innerOpen} title="内层确认" onClose={() => setInnerOpen(false)}>
        <p>内层正文</p>
      </Dialog>
    </>
  );
}

describe("overlay stack focus and nesting contract", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it.each([["dialog"], ["drawer"]] as const)(
    "keeps focus in a parent-controlled field while the parent re-renders (%s)",
    (kind) => {
      render(<InlineCloseFieldHarness kind={kind} />);
      const field = screen.getByLabelText("批准说明");
      field.focus();
      expect(field).toHaveFocus();

      fireEvent.change(field, { target: { value: "审" } });
      expect(field).toHaveFocus();
      fireEvent.change(field, { target: { value: "审核" } });
      expect(field).toHaveFocus();
      expect((field as HTMLTextAreaElement).value).toBe("审核");
      // The initial focus target may be re-read only on the open boundary.
      expect(screen.getByRole("button", { name: kind === "dialog" ? "关闭" : "关闭抽屉" })).not.toHaveFocus();
    },
  );

  it.each([["dialog"], ["drawer"]] as const)(
    "closes only the topmost overlay on one Escape (Dialog → %s)",
    (inner) => {
      render(<NestedOverlayHarness inner={inner} />);
      fireEvent.click(screen.getByRole("button", { name: "打开外层" }));
      expect(screen.getByRole("dialog", { name: "外层工作区" })).toBeInTheDocument();

      fireEvent.click(screen.getByRole("button", { name: "打开内层" }));
      expect(screen.getByRole("dialog", { name: "内层确认" })).toBeInTheDocument();

      fireEvent.keyDown(window, { key: "Escape" });
      expect(screen.queryByRole("dialog", { name: "内层确认" })).toBeNull();
      // The outer layer is still mounted and becomes interactive again.
      const outer = screen.getByRole("dialog", { name: "外层工作区" });
      expect(outer).toBeInTheDocument();
      expect(outer.closest("[data-overlay-active]")).toHaveAttribute("data-overlay-active", "true");

      fireEvent.keyDown(window, { key: "Escape" });
      expect(screen.queryByRole("dialog", { name: "外层工作区" })).toBeNull();
    },
  );

  it("closes only the topmost Dialog when a Drawer is the outer layer", () => {
    render(<NestedDrawerOuterHarness />);
    fireEvent.click(screen.getByRole("button", { name: "打开外层抽屉" }));
    fireEvent.click(screen.getByRole("button", { name: "打开内层确认" }));
    expect(screen.getByRole("dialog", { name: "内层确认" })).toBeInTheDocument();

    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "内层确认" })).toBeNull();
    expect(screen.getByRole("dialog", { name: "外层抽屉" })).toBeInTheDocument();
  });

  it("keeps the lower layer hidden and non-interactive until it becomes topmost", () => {
    render(<NestedOverlayHarness inner="dialog" />);
    fireEvent.click(screen.getByRole("button", { name: "打开外层" }));
    fireEvent.click(screen.getByRole("button", { name: "打开内层" }));

    const outerBackdrop = screen
      .getByRole("dialog", { name: "外层工作区", hidden: true })
      .closest("[data-overlay-active]");
    const innerBackdrop = screen
      .getByRole("dialog", { name: "内层确认" })
      .closest("[data-overlay-active]");
    expect(outerBackdrop).toHaveAttribute("data-overlay-active", "false");
    expect(outerBackdrop).toHaveAttribute("aria-hidden", "true");
    expect(innerBackdrop).toHaveAttribute("data-overlay-active", "true");
    expect(innerBackdrop).not.toHaveAttribute("aria-hidden");
  });

  it("traps Tab inside the topmost layer instead of walking into the hidden one", () => {
    render(<NestedOverlayHarness inner="dialog" />);
    fireEvent.click(screen.getByRole("button", { name: "打开外层" }));
    fireEvent.click(screen.getByRole("button", { name: "打开内层" }));

    const close = screen.getByRole("button", { name: "关闭" });
    const confirm = screen.getByRole("button", { name: "确认关闭" });
    close.focus();
    fireEvent.keyDown(window, { key: "Tab" });
    expect(confirm).toHaveFocus();
    fireEvent.keyDown(window, { key: "Tab" });
    expect(close).toHaveFocus();
    fireEvent.keyDown(window, { key: "Tab", shiftKey: true });
    expect(confirm).toHaveFocus();
    expect(screen.getByRole("button", { name: "打开内层", hidden: true })).not.toHaveFocus();
  });

  it("restores focus to the element that opened the inner overlay", () => {
    render(<NestedOverlayHarness inner="drawer" />);
    fireEvent.click(screen.getByRole("button", { name: "打开外层" }));
    const opener = screen.getByRole("button", { name: "打开内层" });
    opener.focus();
    fireEvent.click(opener);
    expect(screen.getByRole("button", { name: "关闭抽屉" })).toHaveFocus();

    fireEvent.keyDown(window, { key: "Escape" });
    expect(opener).toHaveFocus();
  });

  it("keeps the whole stack when the dirty guard refuses the close", () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    function GuardedHarness() {
      const [outerOpen, setOuterOpen] = useState(false);
      const [innerOpen, setInnerOpen] = useState(false);
      return (
        <>
          <button type="button" onClick={() => setOuterOpen(true)}>打开外层</button>
          <Dialog open={outerOpen} title="外层工作区" onClose={() => setOuterOpen(false)}>
            <button type="button" onClick={() => setInnerOpen(true)}>打开内层</button>
          </Dialog>
          <Dialog
            open={innerOpen}
            title="内层确认"
            dirtyGuard
            onClose={() => setInnerOpen(false)}
          >
            <p>内层正文</p>
          </Dialog>
        </>
      );
    }
    render(<GuardedHarness />);
    fireEvent.click(screen.getByRole("button", { name: "打开外层" }));
    fireEvent.click(screen.getByRole("button", { name: "打开内层" }));

    fireEvent.keyDown(window, { key: "Escape" });
    // The guarded top layer refuses: neither layer may leave the stack.
    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("dialog", { name: "内层确认" })).toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "外层工作区", hidden: true })).toBeInTheDocument();

    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.getByRole("dialog", { name: "内层确认" })).toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "外层工作区", hidden: true })).toBeInTheDocument();

    confirmSpy.mockReturnValue(true);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "内层确认" })).toBeNull();
    expect(screen.getByRole("dialog", { name: "外层工作区" })).toBeInTheDocument();
  });

  it("releases the shared scroll lock exactly once for two stacked layers", () => {
    const originalBody = document.body.style.overflow;
    const originalHtml = document.documentElement.style.overflow;
    document.body.style.overflow = "auto";
    document.documentElement.style.overflow = "auto";

    function ScrollLockHarness() {
      const [first, setFirst] = useState(false);
      const [second, setSecond] = useState(false);
      return (
        <>
          <button type="button" onClick={() => setFirst(true)}>打开第一层</button>
          <Dialog open={first} title="第一层" onClose={() => setFirst(false)}>
            <button type="button" onClick={() => setSecond(true)}>打开第二层</button>
          </Dialog>
          <Drawer open={second} title="第二层" onClose={() => setSecond(false)}>
            <p>第二层正文</p>
          </Drawer>
        </>
      );
    }

    render(<ScrollLockHarness />);
    fireEvent.click(screen.getByRole("button", { name: "打开第一层" }));
    fireEvent.click(screen.getByRole("button", { name: "打开第二层" }));
    expect(document.body.style.overflow).toBe("hidden");
    expect(document.documentElement.style.overflow).toBe("hidden");

    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "第二层" })).toBeNull();
    // The first layer still holds the lock; it must not be released early.
    expect(document.body.style.overflow).toBe("hidden");

    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "第一层" })).toBeNull();
    expect(document.body.style.overflow).toBe("auto");
    expect(document.documentElement.style.overflow).toBe("auto");

    // The depth counter must be back at zero: a fresh open/close cycle still
    // restores the original value instead of leaking "hidden".
    fireEvent.click(screen.getByRole("button", { name: "打开第一层" }));
    expect(document.body.style.overflow).toBe("hidden");
    fireEvent.keyDown(window, { key: "Escape" });
    expect(document.body.style.overflow).toBe("auto");

    document.body.style.overflow = originalBody;
    document.documentElement.style.overflow = originalHtml;
  });
});
