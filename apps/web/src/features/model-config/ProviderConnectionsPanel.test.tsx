import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  listProviderConnections,
  revealProviderSecret,
  type ProviderConnection,
} from "../../generated/api";
import { ProviderConnectionsPanel } from "./ProviderConnectionsPanel";

vi.mock("../../generated/api", () => ({
  listProviderConnections: vi.fn(),
  revealProviderSecret: vi.fn(),
  createProviderConnection: vi.fn(),
  deleteProviderConnection: vi.fn(),
  deleteProviderSecret: vi.fn(),
  probeProviderConnection: vi.fn(),
  replaceProviderSecret: vi.fn(),
}));

const connection: ProviderConnection = {
  id: "pc-deepseek",
  code: "deepseek-main",
  title: "DeepSeek 主连接",
  provider_kind: "DEEPSEEK",
  protocol: "OPENAI_COMPATIBLE",
  base_url: "https://api.deepseek.com/v1",
  model: "deepseek-chat",
  credential_source: "WINDOWS_CREDENTIAL_MANAGER",
  credential_ref: "provider-pc-deepseek",
  environment_variable_name: null,
  has_secret: true,
  masked_secret: "sk-••••••••9x2",
  status: "ACTIVE",
  last_probe: { status: "OK", at: "2026-08-25T00:00:00Z", summary: {} },
  revision: 1,
};

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><ProviderConnectionsPanel /></QueryClientProvider>);
}

describe("ProviderConnectionsPanel", () => {
  beforeEach(() => {
    vi.mocked(listProviderConnections).mockReset().mockResolvedValue({ items: [connection] });
    vi.mocked(revealProviderSecret).mockReset().mockResolvedValue({ secret: "sk-live-secret", expires_in_seconds: 60 });
  });

  it("only reveals on demand and clears plaintext on hide/blur", async () => {
    renderPanel();
    await screen.findByRole("heading", { name: "连接管理" });
    expect(screen.getByLabelText("当前密钥").textContent).toBe("sk-••••••••9x2");

    fireEvent.click(screen.getByRole("button", { name: "查看" }));
    await waitFor(() => expect(screen.getByLabelText("当前密钥").textContent).toBe("sk-live-secret"));
    fireEvent.blur(window);
    expect(screen.getByLabelText("当前密钥").textContent).toBe("sk-••••••••9x2");
  });
});
