import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { Suspense } from "react";
import ChatPage from "@/app/assistants/[assistantId]/chat/page";
import * as api from "@/lib/api";
import questionRequestFixture from "./fixtures/question_request.json";

// Mock API methods
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    getCurrentUser: vi.fn(),
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

describe("ChatPage Component (Phase 3 UI First)", () => {
  const assistantId = "00000000-0000-0000-0000-000000000001";

  beforeEach(() => {
    vi.clearAllMocks();
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

  it("renders input box, send button, and handles answered response with source chips", async () => {
    vi.mocked(api.askQuestion).mockResolvedValue({
      ok: true,
      status: 200,
      data: {
        request_id: "req-1",
        tenant_id: "ten-1",
        conversation_id: "conv-1",
        message_id: "msg-1",
        status: "answered",
        answer: "Standard clinic hours are 9am to 5pm M-F.",
        citations: ["c1"],
        evidence_type: "text",
        sources: [
          {
            document_title: "Clinic Manual",
            version: "v1",
            page_number: 2,
            evidence_type: "text",
          },
        ],
      },
    });

    await renderChatPage();

    const input = await screen.findByPlaceholderText(/Ask a question about clinic documents/i);
    const sendButton = screen.getByRole("button", { name: /Send/i });

    fireEvent.change(input, { target: { value: "What are the hours?" } });
    fireEvent.click(sendButton);

    // User message should appear
    expect(screen.getByText("What are the hours?")).toBeInTheDocument();

    // Assistant response and source chip should appear
    await waitFor(() => {
      expect(screen.getByText("Standard clinic hours are 9am to 5pm M-F.")).toBeInTheDocument();
      expect(screen.getByText("Clinic Manual")).toBeInTheDocument();
      expect(screen.getByText(/Page 2/i)).toBeInTheDocument();
    });
  });

  it("renders visual badge when evidence_type is visual", async () => {
    vi.mocked(api.askQuestion).mockResolvedValue({
      ok: true,
      status: 200,
      data: {
        request_id: "req-2",
        tenant_id: "ten-1",
        conversation_id: "conv-1",
        message_id: "msg-2",
        status: "answered",
        answer: "The fax number is 555-0199.",
        citations: ["c2"],
        evidence_type: "visual",
        sources: [
          {
            document_title: "Prior Auth Scanned Fax",
            version: "v1",
            page_number: 1,
            evidence_type: "visual",
          },
        ],
      },
    });

    await renderChatPage();

    const input = await screen.findByPlaceholderText(/Ask a question/i);
    fireEvent.change(input, { target: { value: "Fax number?" } });
    fireEvent.click(screen.getByRole("button", { name: /Send/i }));

    await waitFor(() => {
      expect(screen.getByText("The fax number is 555-0199.")).toBeInTheDocument();
      expect(screen.getByText("Visual Reading")).toBeInTheDocument();
    });
  });

  it("renders refusal response", async () => {
    vi.mocked(api.askQuestion).mockResolvedValue({
      ok: true,
      status: 200,
      data: {
        request_id: "req-3",
        tenant_id: "ten-1",
        conversation_id: "conv-1",
        message_id: "msg-3",
        status: "refusal",
        answer: "I am unable to answer this question because insufficient validated evidence was found.",
        citations: [],
      },
    });

    await renderChatPage();

    const input = await screen.findByPlaceholderText(/Ask a question/i);
    fireEvent.change(input, { target: { value: "Unknown topic" } });
    fireEvent.click(screen.getByRole("button", { name: /Send/i }));

    await waitFor(() => {
      expect(screen.getByText(/insufficient validated evidence/i)).toBeInTheDocument();
      expect(screen.getByText(/Refusal/i)).toBeInTheDocument();
    });
  });

  it("renders conflicting evidence with both sources", async () => {
    vi.mocked(api.askQuestion).mockResolvedValue({
      ok: true,
      status: 200,
      data: {
        request_id: "req-4",
        tenant_id: "ten-1",
        conversation_id: "conv-1",
        message_id: "msg-4",
        status: "conflicting",
        answer: "Contradictory evidence detected across active documents.",
        citations: ["c1", "c2"],
        conflict_sources: [
          {
            document_title: "Fee Schedule A",
            version: "v1",
            page_number: 1,
            evidence_type: "text",
          },
          {
            document_title: "Insurance Policy B",
            version: "v2",
            page_number: 3,
            evidence_type: "text",
          },
        ],
      },
    });

    await renderChatPage();

    const input = await screen.findByPlaceholderText(/Ask a question/i);
    fireEvent.change(input, { target: { value: "Copay amount" } });
    fireEvent.click(screen.getByRole("button", { name: /Send/i }));

    await waitFor(() => {
      expect(screen.getByText(/Contradictory evidence detected/i)).toBeInTheDocument();
      expect(screen.getByText("Conflicting Sources")).toBeInTheDocument();
      expect(screen.getByText("Fee Schedule A")).toBeInTheDocument();
      expect(screen.getByText("Insurance Policy B")).toBeInTheDocument();
    });
  });

  it("renders safe failure with public error reference", async () => {
    vi.mocked(api.askQuestion).mockResolvedValue({
      ok: true,
      status: 200,
      data: {
        request_id: "req-5",
        tenant_id: "ten-1",
        conversation_id: "conv-1",
        message_id: "msg-5",
        status: "safe_failure",
        answer: "A system error occurred while processing your question.",
        citations: [],
        public_error_ref: "ERR-987654",
      },
    });

    await renderChatPage();

    const input = await screen.findByPlaceholderText(/Ask a question/i);
    fireEvent.change(input, { target: { value: "Cause failure" } });
    fireEvent.click(screen.getByRole("button", { name: /Send/i }));

    expect(await screen.findByText("System Error")).toBeInTheDocument();
    expect(screen.getByText(/ERR-987654/)).toBeInTheDocument();
  });

  it("shows fixed safe message on unknown response type", async () => {
    vi.mocked(api.askQuestion).mockResolvedValue({
      ok: true,
      status: 200,
      data: {
        request_id: "req-6",
        tenant_id: "ten-1",
        conversation_id: "conv-1",
        message_id: "msg-6",
        status: "unexpected_alien_status",
        answer: "Raw mysterious text",
        citations: [],
      },
    });

    await renderChatPage();

    const input = await screen.findByPlaceholderText(/Ask a question/i);
    fireEvent.change(input, { target: { value: "Weird status" } });
    fireEvent.click(screen.getByRole("button", { name: /Send/i }));

    await waitFor(() => {
      expect(screen.getByText("Received an unexpected response format. Please try again.")).toBeInTheDocument();
    });
  });

  it("shows masked error when API call fails", async () => {
    vi.mocked(api.askQuestion).mockResolvedValue({
      ok: false,
      status: 500,
      code: "INTERNAL_SERVER_ERROR",
    });

    await renderChatPage();

    const input = await screen.findByPlaceholderText(/Ask a question/i);
    fireEvent.change(input, { target: { value: "Crash server" } });
    fireEvent.click(screen.getByRole("button", { name: /Send/i }));

    await waitFor(() => {
      expect(screen.getByText(/Unable to complete question \(INTERNAL_SERVER_ERROR\)/i)).toBeInTheDocument();
    });
  });

  it("shared contract: askQuestion sends JSON body matching question_request.json with application/json header", async () => {
    const realApi = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
    const originalFetch = globalThis.fetch;
    let capturedUrl = "";
    let capturedInit: RequestInit | undefined;

    globalThis.fetch = vi.fn().mockImplementation(async (url: string | URL | Request, init?: RequestInit) => {
      capturedUrl = String(url);
      capturedInit = init;
      return new Response(JSON.stringify({ status: "answered", answer: "ok" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });

    try {
      await realApi.askQuestion(
        questionRequestFixture.assistant_id,
        questionRequestFixture.question_text,
        questionRequestFixture.conversation_id
      );

      expect(capturedUrl).toContain("/v1/questions");
      expect(capturedInit?.method).toBe("POST");

      // Verify Content-Type is application/json
      const headers = new Headers(capturedInit?.headers);
      expect(headers.get("Content-Type")).toBe("application/json");

      // Verify exact JSON body equals shared fixture
      const parsedBody = JSON.parse(String(capturedInit?.body));
      expect(parsedBody).toEqual(questionRequestFixture);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});
