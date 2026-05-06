import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import LoginPage from "./page";

const replace = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace }),
}));

describe("LoginPage dev auth bypass", () => {
  beforeEach(() => {
    replace.mockReset();
    vi.restoreAllMocks();
  });

  it("redirects to deals when local dev session creation succeeds", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true })
    );

    render(<LoginPage />);

    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith("/api/session/dev", { method: "POST" });
      expect(replace).toHaveBeenCalledWith("/deals");
    });
  });

  it("falls back to normal API-key form when dev session creation is unavailable", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: false, status: 404 })
    );

    render(<LoginPage />);

    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith("/api/session/dev", { method: "POST" });
    });
    expect(replace).not.toHaveBeenCalled();
    expect(screen.getByLabelText("API Key")).not.toBeNull();
    expect(screen.queryByText("Failed to authenticate")).toBeNull();
  });
});
