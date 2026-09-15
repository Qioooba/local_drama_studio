import { beforeEach, describe, expect, it } from "vitest";
import { draftRegistry } from "./draftRegistry";
import { settleDirtyDrafts } from "./settleDirtyDrafts";

function registerDirty(
  ownerId: string,
  overrides: Partial<Parameters<typeof draftRegistry.register>[0]> = {},
) {
  return draftRegistry.register({
    ownerId,
    entityKey: overrides.entityKey ?? ownerId,
    version: overrides.version ?? 1,
    dirty: overrides.dirty ?? true,
    save: overrides.save,
    discard: overrides.discard,
  });
}

describe("R01 draft coordinator boundaries", () => {
  beforeEach(() => {
    draftRegistry.clear();
  });

  it("A 保存完成，B 保存期间 A 再次变脏 → 不导航，A 的新编辑存在", async () => {
    const ha = registerDirty("a", { entityKey: "草稿 A" });
    const hb = registerDirty("b", {
      entityKey: "草稿 B",
      save: async (v: number) => {
        draftRegistry.update(ha, { version: 2, dirty: true });
        draftRegistry.update(hb, { version: 1, dirty: false });
        return { status: "saved", savedVersion: v };
      },
    });
    // A saves cleanly first; B redirties A during its own save.
    draftRegistry.update(ha, {
      version: 1,
      dirty: true,
      save: async (v: number) => {
        draftRegistry.update(ha, { version: 1, dirty: false });
        return { status: "saved", savedVersion: v };
      },
    });
    const result = await settleDirtyDrafts(draftRegistry, "save");
    expect(result.allowed).toBe(false);
    expect(draftRegistry.get("a")?.dirty).toBe(true);
  });

  it("保存期间新增 owner C → 不导航，C 没被偷偷自动保存", async () => {
    let savedC = false;
    const ha = registerDirty("a", {
      entityKey: "草稿 A",
      save: async (v: number) => {
        draftRegistry.register({
          ownerId: "c",
          entityKey: "草稿 C",
          version: 1,
          dirty: true,
          save: async () => {
            savedC = true;
            return { status: "saved", savedVersion: 1 };
          },
        });
        draftRegistry.update(ha, { version: 1, dirty: false });
        return { status: "saved", savedVersion: v };
      },
    });
    void ha;
    const result = await settleDirtyDrafts(draftRegistry, "save");
    expect(result.allowed).toBe(false);
    expect(savedC).toBe(false);
    expect(draftRegistry.get("c")?.dirty).toBe(true);
  });

  it("savedVersion 与提交版本不同 → 不导航", async () => {
    registerDirty("a", {
      entityKey: "草稿 A",
      save: async () => ({ status: "saved", savedVersion: 999 }),
    });
    const result = await settleDirtyDrafts(draftRegistry, "save");
    expect(result.allowed).toBe(false);
    if (!result.allowed) expect(result.code).toBe("DRAFT_ACK_MISMATCH");
  });

  it("当前 owner 保存时出现新编辑 → 新编辑保留，本次停止", async () => {
    const ha = registerDirty("a", {
      entityKey: "草稿 A",
      save: async (v: number) => {
        draftRegistry.update(ha, { version: 2, dirty: true });
        return { status: "saved", savedVersion: v };
      },
    });
    void ha;
    const result = await settleDirtyDrafts(draftRegistry, "save");
    expect(result.allowed).toBe(false);
    expect(draftRegistry.get("a")?.version).toBe(2);
    expect(draftRegistry.get("a")?.dirty).toBe(true);
  });

  it("旧实例 cleanup 晚到 → 不影响新 token 注册", async () => {
    const oldHandle = draftRegistry.register({
      ownerId: "a",
      entityKey: "草稿 A",
      version: 1,
      dirty: true,
    });
    const newHandle = draftRegistry.register({
      ownerId: "a",
      entityKey: "草稿 A",
      version: 1,
      dirty: true,
      save: async (v: number) => {
        draftRegistry.update(newHandle, { version: 1, dirty: false });
        return { status: "saved", savedVersion: v };
      },
    });
    // Late cleanup from the old mount must be ignored.
    draftRegistry.unregister(oldHandle);
    expect(draftRegistry.get("a")?.registrationToken).toBe(newHandle.token);
    const result = await settleDirtyDrafts(draftRegistry, "save");
    expect(result.allowed).toBe(true);
  });

  it("owner 处理期间卸载 → 不把缺失当成功", async () => {
    const ha = registerDirty("a", {
      entityKey: "草稿 A",
      save: async (v: number) => {
        draftRegistry.unregister(ha);
        return { status: "saved", savedVersion: v };
      },
    });
    void ha;
    const result = await settleDirtyDrafts(draftRegistry, "save");
    expect(result.allowed).toBe(false);
  });

  it("缺少保存回调/旧 void 返回值 → 不放行", async () => {
    registerDirty("a", { entityKey: "草稿 A", save: undefined });
    expect((await settleDirtyDrafts(draftRegistry, "save")).allowed).toBe(false);
    draftRegistry.clear();
    registerDirty("a", {
      entityKey: "草稿 A",
      save: (async () => undefined) as never,
    });
    const result = await settleDirtyDrafts(draftRegistry, "save");
    expect(result.allowed).toBe(false);
  });

  it("A 保存后 B 的 callback 更新 → 使用最新 callback，不调用旧闭包", async () => {
    let staleCalled = false;
    let latestCalled = false;
    const ha = registerDirty("a", {
      entityKey: "草稿 A",
      save: async (v: number) => {
        draftRegistry.update(hb, {
          version: 1,
          dirty: true,
          save: async (vv: number) => {
            latestCalled = true;
            draftRegistry.update(hb, { version: 1, dirty: false });
            return { status: "saved", savedVersion: vv };
          },
        });
        draftRegistry.update(ha, { version: 1, dirty: false });
        return { status: "saved", savedVersion: v };
      },
    });
    const hb = registerDirty("b", {
      entityKey: "草稿 B",
      save: async () => {
        staleCalled = true;
        throw new Error("stale callback");
      },
    });
    void ha;
    void hb;
    const result = await settleDirtyDrafts(draftRegistry, "save");
    expect(result.allowed).toBe(true);
    expect(latestCalled).toBe(true);
    expect(staleCalled).toBe(false);
  });

  it("写入成功但仍脏 → STILL_DIRTY，不伪装成功", async () => {
    registerDirty("a", {
      entityKey: "草稿 A",
      save: async (v: number) => ({ status: "saved", savedVersion: v }),
    });
    // Producer claims saved but never cleared dirty.
    const result = await settleDirtyDrafts(draftRegistry, "save");
    // Our fixture clears? No, this fixture does not clear, so STILL_DIRTY.
    expect(result.allowed).toBe(false);
    if (!result.allowed) expect(result.code).toBe("DRAFT_STILL_DIRTY");
  });

  it("放弃时产生新版本 → 不清掉新版本", async () => {
    const ha = registerDirty("a", {
      entityKey: "草稿 A",
      discard: async (v: number) => {
        draftRegistry.update(ha, { version: 2, dirty: true });
        return { status: "discarded", discardedVersion: v };
      },
    });
    void ha;
    const result = await settleDirtyDrafts(draftRegistry, "discard");
    expect(result.allowed).toBe(false);
    expect(draftRegistry.get("a")?.version).toBe(2);
  });

  it("token 在处理期间变化 → 阻断", async () => {
    const ha = registerDirty("a", {
      entityKey: "草稿 A",
      save: async (v: number) => {
        // Simulate remount: new token, clean.
        draftRegistry.register({ ownerId: "a", entityKey: "草稿 A", version: 1, dirty: false });
        return { status: "saved", savedVersion: v };
      },
    });
    void ha;
    const result = await settleDirtyDrafts(draftRegistry, "save");
    expect(result.allowed).toBe(false);
  });

  it("clean owner 保留挂载，dirty=false 不删除注册", async () => {
    const handle = registerDirty("a", { entityKey: "草稿 A" });
    draftRegistry.update(handle, { version: 1, dirty: false });
    expect(draftRegistry.get("a")).toBeDefined();
    expect(draftRegistry.getDirty().length).toBe(0);
    const result = await settleDirtyDrafts(draftRegistry, "save");
    expect(result.allowed).toBe(true);
  });
});
