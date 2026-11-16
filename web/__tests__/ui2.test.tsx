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

describe("UI-2: Every Answer Type in Chat, Status Lines & Demo Scenarios", () => {
  const liveAssistantId = DEV_DEFAULT_ASSISTANT_ID;
  const demoAssistantId = "00000000-0000-0000-0002-000000000002"; // Pinecrest Medical Group
  const originalEnv = process.env.NEXT_PUBLIC_DATA_SOURCE;

  beforeEach(() => {
    vi.clearAllMocks();
    process.env.NEXT_PUBLIC_DATA_SOURCE = "live";
    vi.mocked(api.getCurrentUser).mockResolvedValue({
      ok: true,
      status: 200,
      data: {
        id: "usr-staff-1",
        email: "staff@clinic.com",
        tenant_id: "ten-maple",
      },
    });
  });

  afterEach(() => {
    process.env.NEXT_PUBLIC_DATA_SOURCE = originalEnv;
    vi.restoreAllMocks();
  });

  async function renderChatPage(assistantId: string = liveAssistantId) {
    await act(async () => {
      render(
        <Suspense fallback={<div>Loading...</div>}>
          <ChatPage params={Promise.resolve({ assistantId })} />
        </Suspense>
      );
    });
  }

  // 1. Answer with source chips (document, version, page) & source language label
  it("renders standard answer with source chips (doc, version, page) and source language label", async () => {
    vi.mocked(api.askQuestion).mockResolvedValue({
      ok: true,
      status: 200,
      data: {
        request_id: "req-tf-003",
        tenant_id: "ten-maple",
        conversation_id: "conv-1",
        message_id: "msg-tf-003",
        status: "answered",
        answer: "Prior authorization requests for outpatient surgery require submission at least 5 business days in advance.",
        citations: ["chunk-1"],
        evidence_type: "text",
        sources: [
          {
            document_title: "Prior Auth Guide",
            version: "v1",
            page_number: 2,
            evidence_type: "text",
            language: "Spanish",
          },
        ],
      },
    });

    await renderChatPage(liveAssistantId);

    const input = await screen.findByPlaceholderText(/Ask a question about clinic documents/i);
    const sendButton = screen.getByRole("button", { name: /Send/i });

    fireEvent.change(input, { target: { value: "How far in advance for outpatient surgery?" } });
    fireEvent.click(sendButton);

    await waitFor(() => {
      expect(screen.getByText(/Prior authorization requests for outpatient surgery require submission/i)).toBeInTheDocument();
      expect(screen.getByText("Prior Auth Guide")).toBeInTheDocument();
      expect(screen.getByText(/v1/)).toBeInTheDocument();
      expect(screen.getByText(/Page 2/i)).toBeInTheDocument();
      expect(screen.getByText(/Source \(Spanish\)|\[Spanish\]/i)).toBeInTheDocument();
    });
  });

  // 2. Table answer citing sheet and row (Query flow, Answer modes "table")
  it("renders table answer citing sheet and row", async () => {
    vi.mocked(api.askQuestion).mockResolvedValue({
      ok: true,
      status: 200,
      data: {
        request_id: "req-tl-011",
        tenant_id: "ten-maple",
        conversation_id: "conv-1",
        message_id: "msg-tl-011",
        status: "answered",
        answer_mode: "table",
        answer: "Annual deductible for Crestview Health Gold Tier is $500.00.",
        citations: ["row-gold-4"],
        evidence_type: "table",
        sources: [
          {
            document_title: "accepted_plans.csv",
            version: "v1",
            page_number: 1,
            evidence_type: "table",
            sheet_name: "Plans",
            row_number: 4,
          },
        ],
      },
    });

    await renderChatPage(liveAssistantId);

    const input = await screen.findByPlaceholderText(/Ask a question about clinic documents/i);
    fireEvent.change(input, { target: { value: "What is the deductible for Crestview Gold?" } });
    fireEvent.click(screen.getByRole("button", { name: /Send/i }));

    await waitFor(() => {
      expect(screen.getByText(/Annual deductible for Crestview Health Gold Tier is \$500\.00/i)).toBeInTheDocument();
      expect(screen.getByText("accepted_plans.csv")).toBeInTheDocument();
      expect(screen.getByText(/Sheet:\s*Plans/i)).toBeInTheDocument();
      expect(screen.getByText(/Row\s*4/i)).toBeInTheDocument();
    });
  });

  // 3. Visual answer marked as read from an image with a page thumbnail placeholder frame
  it("renders visual answer marked as read from an image with a placeholder frame", async () => {
    vi.mocked(api.askQuestion).mockResolvedValue({
      ok: true,
      status: 200,
      data: {
        request_id: "req-vr-007",
        tenant_id: "ten-maple",
        conversation_id: "conv-1",
        message_id: "msg-vr-007",
        status: "answered",
        answer: "Amoxicillin 500mg capsules, 1 capsule orally three times daily for 10 days.",
        citations: ["vis-chunk-1"],
        evidence_type: "visual",
        sources: [
          {
            document_title: "skewed_fax.pdf",
            version: "v1",
            page_number: 1,
            evidence_type: "visual",
          },
        ],
      },
    });

    await renderChatPage(liveAssistantId);

    const input = await screen.findByPlaceholderText(/Ask a question about clinic documents/i);
    fireEvent.change(input, { target: { value: "What medication was authorized for Alex Morgan?" } });
    fireEvent.click(screen.getByRole("button", { name: /Send/i }));

    await waitFor(() => {
      expect(screen.getByText(/Amoxicillin 500mg capsules/i)).toBeInTheDocument();
      expect(screen.getByText("Visual Reading")).toBeInTheDocument();
      // Placeholder frame for page thumbnail in demo/ui
      expect(screen.getByTestId("page-thumbnail-placeholder")).toBeInTheDocument();
    });
  });

  // 4. Refusal ("I don't have that in Client A's documents.")
  it("renders refusal card with spec refusal text", async () => {
    vi.mocked(api.askQuestion).mockResolvedValue({
      ok: true,
      status: 200,
      data: {
        request_id: "req-noa-002",
        tenant_id: "ten-maple",
        conversation_id: "conv-1",
        message_id: "msg-noa-002",
        status: "refusal",
        answer: "I don't have that in Client A's documents.",
        citations: [],
      },
    });

    await renderChatPage(liveAssistantId);

    const input = await screen.findByPlaceholderText(/Ask a question about clinic documents/i);
    fireEvent.change(input, { target: { value: "Where is the patient parking garage?" } });
    fireEvent.click(screen.getByRole("button", { name: /Send/i }));

    await waitFor(() => {
      expect(screen.getByText(/I don't have that in Client A's documents\./i)).toBeInTheDocument();
    });
  });

  // 5. Conflict "Two documents disagree:" with both citations
  it("renders conflict presentation showing 'Two documents disagree:' and both citations", async () => {
    vi.mocked(api.askQuestion).mockResolvedValue({
      ok: true,
      status: 200,
      data: {
        request_id: "req-conf-001",
        tenant_id: "ten-maple",
        conversation_id: "conv-1",
        message_id: "msg-conf-001",
        status: "conflicting",
        answer: "Two documents disagree: Clinic Policy v1 lists intake fax 555-0101, whereas Intake Protocol v2 specifies 555-0102.",
        citations: ["conf-1", "conf-2"],
        conflict_sources: [
          {
            document_title: "Clinic Policy",
            version: "v1",
            page_number: 4,
            evidence_type: "text",
          },
          {
            document_title: "Intake Protocol",
            version: "v2",
            page_number: 1,
            evidence_type: "text",
          },
        ],
      },
    });

    await renderChatPage(liveAssistantId);

    const input = await screen.findByPlaceholderText(/Ask a question about clinic documents/i);
    fireEvent.change(input, { target: { value: "What is the intake fax number?" } });
    fireEvent.click(screen.getByRole("button", { name: /Send/i }));

    await waitFor(() => {
      expect(screen.getAllByText(/Two documents disagree:/i)[0]).toBeInTheDocument();
      expect(screen.getByText("Clinic Policy")).toBeInTheDocument();
      expect(screen.getByText("Intake Protocol")).toBeInTheDocument();
    });
  });

  // 6. Clarify showing top 3 topics as chips and clicking one asks it
  it("renders clarify with top 3 topic chips and clicking one submits that question", async () => {
    vi.mocked(api.askQuestion).mockResolvedValueOnce({
      ok: true,
      status: 200,
      data: {
        request_id: "req-cla-001",
        tenant_id: "ten-maple",
        conversation_id: "conv-1",
        message_id: "msg-cla-001",
        status: "clarify",
        answer: "Please clarify which department or procedure you are referring to:",
        citations: [],
        clarify_topics: ["Prior Authorization", "Intake Workflow", "Referrals"],
      },
    }).mockResolvedValueOnce({
      ok: true,
      status: 200,
      data: {
        request_id: "req-cla-002",
        tenant_id: "ten-maple",
        conversation_id: "conv-1",
        message_id: "msg-cla-002",
        status: "answered",
        answer: "Prior authorization details for outpatient clinics require 5 days notice.",
        citations: ["c-pa"],
      },
    });

    await renderChatPage(liveAssistantId);

    const input = await screen.findByPlaceholderText(/Ask a question about clinic documents/i);
    fireEvent.change(input, { target: { value: "Help with authorization" } });
    fireEvent.click(screen.getByRole("button", { name: /Send/i }));

    // Chips should appear
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Prior Authorization" })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Intake Workflow" })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Referrals" })).toBeInTheDocument();
    });

    // Clicking a clarify chip asks it
    fireEvent.click(screen.getByRole("button", { name: "Prior Authorization" }));

    await waitFor(() => {
      expect(api.askQuestion).toHaveBeenCalledTimes(2);
      expect(screen.getByText("Prior authorization details for outpatient clinics require 5 days notice.")).toBeInTheDocument();
    });
  });

  // 7. General draft labelled as not from client documents
  it("renders general draft labelled as not from client documents", async () => {
    vi.mocked(api.askQuestion).mockResolvedValue({
      ok: true,
      status: 200,
      data: {
        request_id: "req-gen-001",
        tenant_id: "ten-maple",
        conversation_id: "conv-1",
        message_id: "msg-gen-001",
        status: "answered",
        route: "general",
        answer: "Subject: Appointment Reminder\nDear Patient, this is a reminder for your upcoming appointment.",
        citations: [],
      },
    });

    await renderChatPage(liveAssistantId);

    const input = await screen.findByPlaceholderText(/Ask a question about clinic documents/i);
    fireEvent.change(input, { target: { value: "Draft a polite reminder email" } });
    fireEvent.click(screen.getByRole("button", { name: /Send/i }));

    await waitFor(() => {
      expect(screen.getByText(/Subject: Appointment Reminder/i)).toBeInTheDocument();
      expect(screen.getByText(/Not grounded in client documents|General Task Draft/i)).toBeInTheDocument();
    });
  });

  // 8. Safe failure with public reference
  it("renders safe failure with public reference ERR-xxxx", async () => {
    vi.mocked(api.askQuestion).mockResolvedValue({
      ok: true,
      status: 200,
      data: {
        request_id: "req-err-001",
        tenant_id: "ten-maple",
        conversation_id: "conv-1",
        message_id: "msg-err-001",
        status: "safe_failure",
        answer: "I'm having trouble right now. Reference: ERR-7K42",
        citations: [],
        public_error_ref: "ERR-7K42",
      },
    });

    await renderChatPage(liveAssistantId);

    const input = await screen.findByPlaceholderText(/Ask a question about clinic documents/i);
    fireEvent.change(input, { target: { value: "Trigger failure" } });
    fireEvent.click(screen.getByRole("button", { name: /Send/i }));

    await waitFor(() => {
      expect(screen.getByText(/I'm having trouble right now/i)).toBeInTheDocument();
      expect(screen.getByText(/ERR-7K42/i)).toBeInTheDocument();
    });
  });

  // 9. Unknown-type safe message
  it("renders safe friendly message on unknown response type", async () => {
    vi.mocked(api.askQuestion).mockResolvedValue({
      ok: true,
      status: 200,
      data: {
        request_id: "req-unknown",
        tenant_id: "ten-maple",
        conversation_id: "conv-1",
        message_id: "msg-unk",
        status: "future_unknown_status_code" as any,
        answer: "Some raw unhandled text",
        citations: [],
      },
    });

    await renderChatPage(liveAssistantId);

    const input = await screen.findByPlaceholderText(/Ask a question about clinic documents/i);
    fireEvent.change(input, { target: { value: "Unknown type test" } });
    fireEvent.click(screen.getByRole("button", { name: /Send/i }));

    await waitFor(() => {
      expect(screen.getByText("Unable to complete this request. Please try again.")).toBeInTheDocument();
    });
  });

  // 10. Status lines: only shown in demo assistant, never faked on live assistant
  it("does not fake status lines on live assistant (shows standard loading indicator only)", async () => {
    let resolveAsk: (val: any) => void = () => {};
    const pendingPromise = new Promise((res) => {
      resolveAsk = res;
    });
    vi.mocked(api.askQuestion).mockReturnValue(pendingPromise as any);

    await renderChatPage(liveAssistantId);

    const input = await screen.findByPlaceholderText(/Ask a question about clinic documents/i);
    fireEvent.change(input, { target: { value: "Live question test" } });
    fireEvent.click(screen.getByRole("button", { name: /Send/i }));

    // On live assistant, loading indicator is present
    expect(screen.getByTestId("chat-loading-indicator")).toBeInTheDocument();
    // Must NEVER fake spec status lines on live!
    expect(screen.queryByText("Searching Client A documents...")).not.toBeInTheDocument();
    expect(screen.queryByText("Page 31 is a scanned image, reading it now...")).not.toBeInTheDocument();

    await act(async () => {
      resolveAsk({
        ok: true,
        status: 200,
        data: {
          request_id: "req-live",
          tenant_id: "ten-maple",
          conversation_id: "conv-1",
          message_id: "msg-live",
          status: "answered",
          answer: "Live response text.",
          citations: [],
        },
      });
    });
  });

  it("shows spec status lines in demo assistant during simulated generation", async () => {
    await renderChatPage(demoAssistantId);

    // In demo assistant, ask uses demo fixtures
    const input = await screen.findByPlaceholderText(/Ask a question about clinic documents/i);
    fireEvent.change(input, { target: { value: "What is the front desk fax?" } });
    fireEvent.click(screen.getByRole("button", { name: /Send/i }));

    // Demo assistant demonstrates spec status lines
    await waitFor(() => {
      expect(
        screen.getAllByText(/Searching Client A documents\.\.\.|Searching Pinecrest Medical Group documents\.\.\./i)[0]
      ).toBeInTheDocument();
    });
  });

  // 11. Empty chat state for demo assistant: shows suggested questions from evals/golden/questions.json
  it("shows suggested questions for every answer type in empty chat for demo assistant", async () => {
    await renderChatPage(demoAssistantId);

    // Click "+ New chat" to ensure empty state
    fireEvent.click(screen.getByRole("button", { name: /\+ New (chat|Conversation)/i }));

    // Should render suggested question buttons/chips for quick demonstration
    expect(screen.getByTestId("demo-suggested-questions")).toBeInTheDocument();
    // Check presence of golden questions
    expect(screen.getByText(/How many business days in advance must outpatient surgery prior auths be submitted\?/i)).toBeInTheDocument();
    expect(screen.getByText(/What is the annual deductible for Crestview Health under the Gold Tier plan\?/i)).toBeInTheDocument();
    expect(screen.getByText(/What medication and strength did Dr\. Evelyn Reed authorize/i)).toBeInTheDocument();
    expect(screen.getByText(/Where is the patient parking garage located/i)).toBeInTheDocument();
    expect(screen.getByText(/What is the front desk intake fax number/i)).toBeInTheDocument();
  });
});
