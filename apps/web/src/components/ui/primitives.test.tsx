import { fireEvent, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { describe, expect, it, vi } from "vitest";
import {
  CandidateTray,
  ContextMenu,
  Dialog,
  Drawer,
  EmptyState,
  EntityRail,
  ErrorState,
  Field,
  IconButton,
  Inspector,
  InspectorSection,
  MediaStage,
  MediaThumb,
  Popover,
  PropertyRow,
  ResizablePane,
  Skeleton,
  StatusBadge,
  StickyCommandBar,
  TabPanel,
  Tabs,
  ThreePaneLayout,
  ToastProvider,
  Tooltip,
  useToast,
  VirtualList,
} from "./primitives";


function ToastTestConsumer() {
  const { showToast } = useToast();
  return (
    <button
      type="button"
      onClick={() => showToast({ title: "已保存", message: "镜头修改已生效", tone: "success" })}
    >
      触发提示
    </button>
  );
}

describe("shared UI primitives", () => {
  it("never lets MediaThumb request an original content endpoint", () => {
    const { rerender } = render(<MediaThumb src="/api/v1/media-versions/mv-1/content" alt="镜头图" />);
    expect(screen.queryByRole("img", { name: "镜头图" })).not.toBeInTheDocument();
    expect(screen.getByRole("img", { name: /镜头图：暂无缩略图/ })).toBeInTheDocument();
    rerender(<MediaThumb src="/api/v1/media-versions/mv-1/thumbnail?size=small" alt="镜头图" />);
    expect(screen.getByRole("img", { name: "镜头图" })).toHaveAttribute("loading", "lazy");
    rerender(<MediaThumb src="/api/v1/media-versions/mv-1" alt="镜头图" />);
    expect(screen.getByRole("img", { name: /镜头图：暂无缩略图/ })).toBeInTheDocument();
  });

  it("provides semantic state, inspector and property building blocks", () => {
    render(
      <>
        <StatusBadge tone="success">已批准</StatusBadge>
        <EmptyState title="没有镜头" />
        <ErrorState description="连接失败" />
        <Skeleton lines={2} />
        <InspectorSection title="构图">
          <PropertyRow label="景别">
            <input aria-label="景别" />
          </PropertyRow>
        </InspectorSection>
        <Tooltip content="不可用原因">
          <button>生成</button>
        </Tooltip>
      </>
    );
    expect(screen.getByText("已批准")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("连接失败");
    expect(screen.getByRole("status")).toHaveAttribute("aria-label", "正在加载");
    expect(screen.getByText("构图")).toBeInTheDocument();
    expect(screen.getByRole("tooltip")).toHaveTextContent("不可用原因");
  });

  it("closes Dialog with Escape and restores focus", () => {
    const close = vi.fn();
    const { rerender } = render(
      <>
        <button>触发器</button>
        <Dialog open={false} title="确认" onClose={close} footer={<button>提交</button>}>
          正文
        </Dialog>
      </>
    );
    screen.getByRole("button", { name: "触发器" }).focus();
    rerender(
      <>
        <button>触发器</button>
        <Dialog open title="确认" onClose={close} footer={<button>提交</button>}>
          正文
        </Dialog>
      </>
    );
    expect(screen.getByRole("dialog")).toHaveAccessibleName("确认");
    expect(screen.getByRole("dialog").closest(".ui-dialog-backdrop")?.parentElement).toBe(document.body);
    expect(document.documentElement.style.overflow).toBe("hidden");
    expect(document.body.style.overflow).toBe("hidden");
    expect(screen.getByRole("button", { name: "关闭" })).toHaveFocus();
    fireEvent.keyDown(window, { key: "Tab", shiftKey: true });
    expect(screen.getByRole("button", { name: "提交" })).toHaveFocus();
    fireEvent.keyDown(window, { key: "Tab" });
    expect(screen.getByRole("button", { name: "关闭" })).toHaveFocus();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(close).toHaveBeenCalledOnce();
  });

  it("handles Drawer sliding panel, keyboard Escape and dirty guard", () => {
    const close = vi.fn();
    const { rerender } = render(
      <Drawer open={false} title="检查器抽屉" onClose={close}>
        抽屉内容
      </Drawer>
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    rerender(
      <Drawer open={true} title="检查器抽屉" onClose={close} dirtyGuard={true}>
        抽屉内容
      </Drawer>
    );
    expect(screen.getByRole("dialog")).toHaveAccessibleName("检查器抽屉");
    expect(screen.getByRole("dialog").closest(".ui-drawer-backdrop")?.parentElement).toBe(document.body);
    expect(screen.getByRole("button", { name: "关闭抽屉" })).toHaveFocus();

    // Confirm cancel on dirty guard
    vi.spyOn(window, "confirm").mockReturnValueOnce(false);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(close).not.toHaveBeenCalled();

    // Confirm approve on dirty guard
    vi.spyOn(window, "confirm").mockReturnValueOnce(true);
    fireEvent.click(screen.getByRole("button", { name: "关闭抽屉" }));
    expect(close).toHaveBeenCalledOnce();
  });

  it("navigates Tabs with keyboard arrows and renders selected TabPanel", () => {
    const onChange = vi.fn();
    const tabs = [
      { id: "gen", label: "生成" },
      { id: "bridge", label: "镜头桥" },
      { id: "assets", label: "资产", disabled: true },
    ];
    render(
      <Tabs items={tabs} selectedId="gen" onChange={onChange}>
        <TabPanel id="gen" selectedId="gen">
          生成控制台
        </TabPanel>
        <TabPanel id="bridge" selectedId="gen">
          镜头桥面板
        </TabPanel>
      </Tabs>
    );

    const activeTab = screen.getByRole("tab", { name: "生成" });
    expect(activeTab).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tabpanel")).toHaveTextContent("生成控制台");

    fireEvent.keyDown(screen.getByRole("tablist"), { key: "ArrowRight" });
    expect(onChange).toHaveBeenCalledWith("bridge");
    expect(screen.getByRole("tab", { name: "镜头桥" })).toHaveFocus();
  });

  it("creates unique tab and panel ids across repeated tab sets", () => {
    render(<>
      <Tabs items={[{ id: "shared", label: "第一组" }]} selectedId="shared" onChange={() => undefined}>
        <TabPanel id="shared" selectedId="shared">第一面板</TabPanel>
      </Tabs>
      <Tabs items={[{ id: "shared", label: "第二组" }]} selectedId="shared" onChange={() => undefined}>
        <TabPanel id="shared" selectedId="shared">第二面板</TabPanel>
      </Tabs>
    </>);
    const tabs = screen.getAllByRole("tab");
    const panels = screen.getAllByRole("tabpanel");
    expect(tabs[0].id).not.toBe(tabs[1].id);
    expect(panels[0].id).not.toBe(panels[1].id);
    expect(tabs[0]).toHaveAttribute("aria-controls", panels[0].id);
    expect(panels[1]).toHaveAttribute("aria-labelledby", tabs[1].id);
  });

  it("handles Popover click outside and keyboard dismissal", () => {
    const close = vi.fn();
    render(
      <div>
        <button type="button">外部按钮</button>
        <Popover open={true} onClose={close}>
          <p>气泡弹层内容</p>
        </Popover>
      </div>
    );
    expect(screen.getByText("气泡弹层内容")).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(close).toHaveBeenCalledOnce();
  });

  it("renders IconButton and Field with accessible labels and errors", () => {
    const click = vi.fn();
    render(
      <>
        <IconButton icon="★" label="收藏镜头" shortcut="Ctrl+D" onClick={click} />
        <Field label="镜头提示词" required hint="支持中文与英文" error="提示词不能为空">
          <input id="prompt-input" />
        </Field>
      </>
    );
    const btn = screen.getByRole("button", { name: "收藏镜头" });
    expect(btn).toHaveAttribute("title", "收藏镜头 (Ctrl+D)");
    fireEvent.click(btn);
    expect(click).toHaveBeenCalledOnce();

    expect(screen.getByText("镜头提示词")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("提示词不能为空");
  });

  it("dispatches and dismisses transient Toast notifications", () => {
    render(
      <ToastProvider>
        <ToastTestConsumer />
      </ToastProvider>
    );
    fireEvent.click(screen.getByRole("button", { name: "触发提示" }));
    expect(screen.getByText("已保存")).toBeInTheDocument();
    expect(screen.getByText("镜头修改已生效")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "关闭通知" }));
    expect(screen.queryByText("已保存")).not.toBeInTheDocument();
  });

  it("renders Workbench ThreePaneLayout, MediaStage, EntityRail, CandidateTray and StickyCommandBar", () => {
    const selectShot = vi.fn();
    const selectCandidate = vi.fn();
    const approveCandidate = vi.fn();
    const save = vi.fn();

    render(
      <ThreePaneLayout
        navRail={
          <EntityRail
            title="分集镜头"
            selectedId="s1"
            onSelect={selectShot}
            items={[{ id: "s1", title: "S01-全景", status: "success" }]}
          />
        }
        mainStage={
          <div>
            <MediaStage
              aspectRatio="16 / 9"
              controls={<button>播放</button>}
              overlay={<div data-testid="overlay">帧锚点</div>}
            />
            <CandidateTray
              selectedId="cand-1"
              onSelect={selectCandidate}
              onApprove={approveCandidate}
              candidates={[{ id: "cand-1", title: "候选 1", status: "info" }]}
            />
          </div>
        }
        inspector={<Inspector title="镜头参数">参数表单</Inspector>}
      />
    );

    expect(screen.getByText("分集镜头")).toBeInTheDocument();
    expect(screen.getByText("S01-全景")).toBeInTheDocument();
    expect(screen.getByTestId("overlay")).toHaveTextContent("帧锚点");
    expect(screen.getByText("候选 1")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "选用" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "选用" }));
    expect(approveCandidate).toHaveBeenCalledWith("cand-1");

    render(
      <StickyCommandBar dirty={true} onSave={save} statusSummary="3 项变动待提交" />
    );
    expect(screen.getByText("未保存改动")).toBeInTheDocument();
    expect(screen.getByText("3 项变动待提交")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "保存 (Ctrl+S)" }));
    expect(save).toHaveBeenCalledOnce();
  });

  it("supports keyboard resizing with bounded separator values", () => {
    render(<ResizablePane primary="导航" secondary="内容" initial={300} min={240} max={320} />);
    const separator = screen.getByRole("separator");
    fireEvent.keyDown(separator, { key: "ArrowRight" });
    expect(separator).toHaveAttribute("aria-valuenow", "316");
    fireEvent.keyDown(separator, { key: "End" });
    expect(separator).toHaveAttribute("aria-valuenow", "320");
    fireEvent.keyDown(separator, { key: "ArrowRight" });
    expect(separator).toHaveAttribute("aria-valuenow", "320");
  });

  it("navigates context actions and exposes disabled reasons", () => {
    const select = vi.fn();
    const close = vi.fn();
    render(
      <ContextMenu
        open
        x={10}
        y={20}
        onClose={close}
        items={[
          { id: "edit", label: "编辑", onSelect: select },
          { id: "delete", label: "删除", disabledReason: "镜头已冻结", onSelect: vi.fn() },
        ]}
      />
    );
    expect(screen.getByRole("menu")).toHaveAccessibleName("快捷菜单");
    expect(screen.getByRole("menuitem", { name: "删除" })).toHaveAttribute("title", "镜头已冻结");
    fireEvent.click(screen.getByRole("menuitem", { name: "编辑" }));
    expect(select).toHaveBeenCalledOnce();
    expect(close).toHaveBeenCalledOnce();
  });

  it("renders virtualized subset for 50+ items and updates on scroll", () => {
    const items = Array.from({ length: 100 }, (_, i) => ({ id: `item-${i}`, text: `条目 ${i}` }));
    render(
      <VirtualList
        items={items}
        itemHeight={40}
        height={200}
        renderItem={(item) => <div>{item.text}</div>}
        keyExtractor={(item) => item.id}
        ariaLabel="测试虚拟列表"
      />
    );
    expect(screen.getByRole("list", { name: "测试虚拟列表" })).toBeInTheDocument();
    // Verify first items are rendered
    expect(screen.getByText("条目 0")).toBeInTheDocument();
    expect(screen.getByText("条目 5")).toBeInTheDocument();
    // Far items should not be mounted yet
    expect(screen.queryByText("条目 90")).not.toBeInTheDocument();
  });
});
