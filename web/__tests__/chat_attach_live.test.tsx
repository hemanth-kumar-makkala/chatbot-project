import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { Suspense } from "react";
import ChatPage from "@/app/assistants/[assistantId]/page";
import * as api from "@/lib/api";
import { DEV_DEFAULT_ASSISTANT_ID } from "@/lib/devDefaults";

// Mock API methods
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    getCurrentUser: vi.fn(),
    uploadSessionDocument: vi.fn(),
    logout: vi.fn(),
  };
});

// Mock Next.js router
vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: vi.fn(),
    replace: vi.fn(),
  }),
  useSearchParams: () => new URLSearchParams(),
}));

describe("Live Chat Attach Flow (Task 3 fix)", () => {
  const assistantId = DEV_DEFAULT_ASSISTANT_ID;

  beforeEach(() => {
    vi.clearAllMocks();
    process.env.NEXT_PUBLIC_DATA_SOURCE = "live";
    vi.mocked(api.getCurrentUser).mockResolvedValue({
      ok: true,
      status: 200,
      data: {
        id: "usr-1",
        email: "staff@clinic.com",
        tenant_id: "ten-1",
      },
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  async function renderChatPage() {
    await act(async () => {
      render(
        <Suspense fallback={<div>Loading...</div>}>
          <ChatPage params={Promise.resolve({ assistantId })} />
        </Suspense>
      );
    });
  }

  it("clicking attach button does not show stale Phase 4 notice and triggers file input", async () => {
    await renderChatPage();

    const attachBtn = await screen.findByTestId("chat-attach-button");
    fireEvent.click(attachBtn);

    // Stale notice must NOT be present
    expect(screen.queryByTestId("session-upload-live-message")).not.toBeInTheDocument();
    expect(
      screen.queryByText(/Session upload needs the session document route/i)
    ).not.toBeInTheDocument();
  });

  it("uploading session document in live mode renders page preview and extraction table", async () => {
    vi.mocked(api.uploadSessionDocument).mockResolvedValue({
      ok: true,
      status: 200,
      data: {
        document_id: "doc-sess-1",
        conversation_id: "conv-1",
        file_name: "skewed_fax.pdf",
        page_count: 1,
        page_image_url: "/v1/assistants/ast-1/conversations/conv-1/documents/doc-sess-1/pages/1/image",
        extracted_fields: [
          {
            name: "patient_name",
            value: "Alex Morgan",
            state: "FILLED_HIGH",
            page_number: 1,
            document_title: "skewed_fax.pdf",
          },
          {
            name: "authorized_medication",
            value: "Amoxicillin 500mg",
            state: "FILLED_HIGH",
            page_number: 1,
            document_title: "skewed_fax.pdf",
          },
        ],
      },
    });

    await renderChatPage();

    const fileInput = await screen.findByTestId("live-file-input");
    const fakeFile = new File(["%PDF-1.4 test"], "skewed_fax.pdf", { type: "application/pdf" });

    await act(async () => {
      fireEvent.change(fileInput, { target: { files: [fakeFile] } });
    });

    expect(api.uploadSessionDocument).toHaveBeenCalledTimes(1);

    await waitFor(() => {
      expect(screen.getByTestId("extraction-split-view")).toBeInTheDocument();
      expect(screen.getByTestId("extraction-page-image")).toBeInTheDocument();
      expect(screen.getByText("Alex Morgan")).toBeInTheDocument();
      expect(screen.getByText("Amoxicillin 500mg")).toBeInTheDocument();
    });
  });
});
