import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import * as client from "./mediaPickerClient";
import { ProjectMediaVersionSelect } from "./ProjectMediaVersionSelect";

it("searches beyond the recent page by character and preserves a selected version outside the results", async () => {
  const catalogue = vi.spyOn(client, "listProjectMedia").mockResolvedValue([]);
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={queryClient}><ProjectMediaVersionSelect projectId="project" value="older-reference" onChange={() => {}} label="参考图" mediaKinds={["IMAGE"]} /></QueryClientProvider>);
  await screen.findByRole("option", { name: /已选择的媒体版本/ });
  fireEvent.change(screen.getByLabelText("搜索参考图"), { target: { value: "林晚" } });
  await waitFor(() => expect(catalogue).toHaveBeenCalledWith("project", "林晚", "IMAGE"));
  expect((screen.getByLabelText("参考图") as HTMLSelectElement).value).toBe("older-reference");
  catalogue.mockRestore();
});
