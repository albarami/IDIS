import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { idis } from "@/lib/idis";

import DataRoomUpload from "./DataRoomUpload";

describe("DataRoomUpload", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("uploads the selected file and shows a safe result summary (ids/name/status only)", async () => {
    const uploadSpy = vi.spyOn(idis.documents, "upload").mockResolvedValue({
      doc_id: "doc-123",
      deal_id: "deal-1",
      doc_type: "PITCH_DECK",
      title: "deck.pdf",
      parse_status: "PARSED",
      source_system: "api-upload",
    });

    render(<DataRoomUpload dealId="deal-1" />);

    const file = new File(["binary-bytes"], "deck.pdf", { type: "application/pdf" });
    fireEvent.change(screen.getByLabelText(/file/i), { target: { files: [file] } });
    fireEvent.change(screen.getByLabelText(/document type/i), { target: { value: "PITCH_DECK" } });
    fireEvent.click(screen.getByRole("button", { name: /upload/i }));

    await waitFor(() => expect(screen.getByText("doc-123")).toBeTruthy());
    expect(uploadSpy).toHaveBeenCalledWith("deal-1", file, {
      filename: "deck.pdf",
      docType: "PITCH_DECK",
    });
    // Safe result: ids + status shown; the raw document bytes are never rendered.
    expect(screen.getByText("PARSED")).toBeTruthy();
    expect(screen.queryByText("binary-bytes")).toBeNull();
  });

  it("shows an error message when the upload fails", async () => {
    vi.spyOn(idis.documents, "upload").mockRejectedValue(new Error("upload boom"));

    render(<DataRoomUpload dealId="deal-1" />);
    const file = new File(["x"], "f.pdf", { type: "application/pdf" });
    fireEvent.change(screen.getByLabelText(/file/i), { target: { files: [file] } });
    fireEvent.click(screen.getByRole("button", { name: /upload/i }));

    await waitFor(() => expect(screen.getByText(/upload boom|failed/i)).toBeTruthy());
  });

  it("does not upload when no file is selected", () => {
    const uploadSpy = vi.spyOn(idis.documents, "upload");
    render(<DataRoomUpload dealId="deal-1" />);
    fireEvent.click(screen.getByRole("button", { name: /upload/i }));
    expect(uploadSpy).not.toHaveBeenCalled();
  });
});
