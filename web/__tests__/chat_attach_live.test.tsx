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
    extractSessionDocument: vi.fn(),
    askQuestion: vi.fn(),
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
    vi.useRealTimers();
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

  it("uploading session document uploads once, calls extraction zero times, and shows document card with retention", async () => {
    vi.mocked(api.uploadSessionDocument).mockResolvedValue({
      ok: true,
      status: 200,
      data: {
        session_document_id: "doc-sess-1",
        conversation_id: "conv-1",
        file_name: "skewed_fax.pdf",
        page_count: 1,
        status: "uploaded",
        retention_hours: 24,
        extracted_fields: [],
        page_image_url: "/v1/assistants/ast-1/conversations/conv-1/documents/doc-sess-1/pages/1/image",
      },
    });

    vi.mocked(api.extractSessionDocument).mockResolvedValue({
      ok: true,
      status: 200,
      data: {
        session_document_id: "doc-sess-1",
        conversation_id: "conv-1",
        file_name: "skewed_fax.pdf",
        page_count: 1,
        status: "extracted",
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
        page_image_url: "/v1/assistants/ast-1/conversations/conv-1/documents/doc-sess-1/pages/1/image",
      },
    });

    await renderChatPage();

    const fileInput = await screen.findByTestId("live-file-input");
    const fakeFile = new File(["%PDF-1.4 test"], "skewed_fax.pdf", { type: "application/pdf" });

    await act(async () => {
      fireEvent.change(fileInput, { target: { files: [fakeFile] } });
    });

    // 1. One attach produces exactly 1 upload request and 0 extraction requests
    expect(api.uploadSessionDocument).toHaveBeenCalledTimes(1);
    expect(api.extractSessionDocument).toHaveBeenCalledTimes(0);

    // 2. Document card is merged into the bottom attachment bar above the input, not in the top chat stream
    await waitFor(() => {
      const inputBar = screen.getByTestId("chat-input-bar");
      const messagesStream = screen.getByTestId("chat-messages-stream");
      const docCard = screen.getByTestId("session-document-card");
      expect(inputBar).toContainElement(docCard);
      expect(messagesStream).not.toContainElement(docCard);
      expect(screen.getByTestId("session-document-indicator")).toHaveTextContent(
        "Session document attached"
      );
      expect(screen.getByTestId("attached-doc-filename")).toHaveTextContent("skewed_fax.pdf");
      expect(screen.getByTestId("attached-doc-page-count")).toHaveTextContent("1 page");
      expect(screen.getByTestId("attached-doc-retention")).toHaveTextContent(
        "Attached to this chat. Deleted after 24 hours"
      );
      expect(screen.getByTestId("extract-fields-button")).toBeInTheDocument();
    });

    // 3. Clicking "Extract fields" calls only extractSessionDocument, never askQuestion, and never posts a user chat message
    const extractBtn = screen.getByTestId("extract-fields-button");
    await act(async () => {
      fireEvent.click(extractBtn);
    });

    expect(api.extractSessionDocument).toHaveBeenCalledTimes(1);
    expect(api.askQuestion).toHaveBeenCalledTimes(0);

    await waitFor(() => {
      expect(screen.getByTestId("extraction-split-view")).toBeInTheDocument();
      expect(screen.getByTestId("extraction-page-image")).toBeInTheDocument();
      expect(screen.getByText("Alex Morgan")).toBeInTheDocument();
      expect(screen.getByText("Amoxicillin 500mg")).toBeInTheDocument();
      // Only 1 chat message rendered (the assistant extraction result), no user chat message posted
      const chatMessages = screen.getAllByTestId("chat-message");
      expect(chatMessages).toHaveLength(1);
      // "reading it now..." disappears after extraction finishes
      expect(screen.queryByTestId("live-extracting-status")).not.toBeInTheDocument();
      expect(screen.queryByTestId("extraction-status-line")).not.toBeInTheDocument();
    });
  });

  it("extraction request shows 'Still working, loading the vision model...' after 10s and removes it on success and on failure", async () => {
    vi.mocked(api.uploadSessionDocument).mockResolvedValue({
      ok: true,
      status: 200,
      data: {
        session_document_id: "doc-sess-slow",
        conversation_id: "conv-1",
        file_name: "skewed_fax.pdf",
        page_count: 1,
        status: "uploaded",
        retention_hours: 24,
        extracted_fields: [],
        page_image_url: "/v1/assistants/ast-1/conversations/conv-1/documents/doc-sess-slow/pages/1/image",
      },
    });

    let resolveExtraction: ((val: unknown) => void) | undefined;
    const delayedPromise = new Promise((resolve) => {
      resolveExtraction = resolve;
    });
    vi.mocked(api.extractSessionDocument).mockReturnValue(delayedPromise as unknown as ReturnType<typeof api.extractSessionDocument>);

    await renderChatPage();

    const fileInput = await screen.findByTestId("live-file-input");
    const fakeFile = new File(["%PDF-1.4 test"], "skewed_fax.pdf", { type: "application/pdf" });

    await act(async () => {
      fireEvent.change(fileInput, { target: { files: [fakeFile] } });
    });

    const extractBtn = await screen.findByTestId("extract-fields-button");

    vi.useFakeTimers({ shouldAdvanceTime: true });

    await act(async () => {
      fireEvent.click(extractBtn);
    });

    // Initial status line
    expect(screen.getByTestId("live-extracting-status")).toHaveTextContent(
      "Page 1 is a scanned image, reading it now..."
    );

    // Fast-forward 10 seconds
    await act(async () => {
      vi.advanceTimersByTime(10000);
    });

    expect(screen.getByTestId("live-extracting-status")).toHaveTextContent(
      "Still working, loading the vision model..."
    );

    // Now resolve delayed extraction with failure first to prove line disappears on failure with clear message
    await act(async () => {
      if (resolveExtraction) {
        resolveExtraction({
          ok: false,
          status: 500,
          code: "EXTRACTION_FAILED",
        });
      }
    });

    expect(screen.queryByTestId("live-extracting-status")).not.toBeInTheDocument();
    expect(screen.queryByText(/Still working, loading the vision model/i)).not.toBeInTheDocument();
    expect(screen.getByTestId("live-extraction-error")).toHaveTextContent(
      /Unable to extract fields \(EXTRACTION_FAILED\)\. Please try again\./i
    );

    // Retry and resolve with success to prove line also disappears on success
    let resolveSuccess: ((val: unknown) => void) | undefined;
    const delayedSuccess = new Promise((resolve) => {
      resolveSuccess = resolve;
    });
    vi.mocked(api.extractSessionDocument).mockReturnValue(delayedSuccess as unknown as ReturnType<typeof api.extractSessionDocument>);

    await act(async () => {
      fireEvent.click(extractBtn);
    });

    await act(async () => {
      vi.advanceTimersByTime(10000);
    });
    expect(screen.getByTestId("live-extracting-status")).toHaveTextContent(
      "Still working, loading the vision model..."
    );

    await act(async () => {
      if (resolveSuccess) {
        resolveSuccess({
          ok: true,
          status: 200,
          data: {
            session_document_id: "doc-sess-slow",
            conversation_id: "conv-1",
            file_name: "skewed_fax.pdf",
            page_count: 1,
            status: "extracted",
            extracted_fields: [
              {
                name: "patient_name",
                value: "Alex Morgan",
                state: "FILLED_HIGH",
                page_number: 1,
                document_title: "skewed_fax.pdf",
              },
            ],
            page_image_url: "/v1/assistants/ast-1/conversations/conv-1/documents/doc-sess-slow/pages/1/image",
          },
        });
      }
    });

    expect(screen.queryByTestId("live-extracting-status")).not.toBeInTheDocument();
    expect(screen.queryByText(/Still working, loading the vision model/i)).not.toBeInTheDocument();
    expect(screen.getByText("Alex Morgan")).toBeInTheDocument();

    vi.useRealTimers();
  });

  it("after attaching a document in a new chat, the question request carries the same conversation_id returned for the upload", async () => {
    const createdConvId = "conv-newly-created-999";
    vi.mocked(api.uploadSessionDocument).mockResolvedValue({
      ok: true,
      status: 200,
      data: {
        session_document_id: "doc-sess-999",
        conversation_id: createdConvId,
        file_name: "skewed_fax.pdf",
        page_count: 1,
        status: "uploaded",
        retention_hours: 24,
        extracted_fields: [],
        page_image_url: `/v1/assistants/${assistantId}/conversations/${createdConvId}/documents/doc-sess-999/pages/1/image`,
      },
    });

    vi.mocked(api.askQuestion).mockResolvedValue({
      ok: true,
      status: 200,
      data: {
        request_id: "req-1",
        tenant_id: "ten-1",
        conversation_id: createdConvId,
        message_id: "msg-1",
        status: "answered",
        answer: "Take 1 capsule by mouth three times daily for 10 days",
        citations: [],
        sources: [
          {
            document_title: "skewed_fax.pdf",
            page_number: 1,
            version: "1.0",
            evidence_type: "visual",
          },
        ],
        route: "session_document",
      },
    });

    await renderChatPage();

    // 1. User attaches document in new chat (initially conversationId is null)
    const fileInput = await screen.findByTestId("live-file-input");
    const fakeFile = new File(["%PDF-1.4 test"], "skewed_fax.pdf", { type: "application/pdf" });

    await act(async () => {
      fireEvent.change(fileInput, { target: { files: [fakeFile] } });
    });

    // Upload was called, returning createdConvId
    expect(api.uploadSessionDocument).toHaveBeenCalledTimes(1);
    await waitFor(() => {
      expect(screen.getByTestId("session-document-card")).toBeInTheDocument();
    });

    // 2. User sends question
    const chatInput = screen.getByPlaceholderText(/Ask a question/i);
    await act(async () => {
      fireEvent.change(chatInput, { target: { value: "what is the dosage in this fax?" } });
    });

    const sendBtn = screen.getByTestId("chat-submit-button");
    await act(async () => {
      fireEvent.click(sendBtn);
    });

    // 3. Question request must carry the exact same conversation_id
    await waitFor(() => {
      expect(api.askQuestion).toHaveBeenCalledTimes(1);
    });

    expect(api.askQuestion).toHaveBeenCalledWith(
      assistantId,
      "what is the dosage in this fax?",
      createdConvId
    );
  });
});
