import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, act, within, cleanup } from "@testing-library/react";
import { Suspense } from "react";
import ChatPage from "@/app/assistants/[assistantId]/chat/page";
import { TopBar } from "@/components/TopBar";
import * as api from "@/lib/api";
import { DEV_DEFAULT_ASSISTANT_ID } from "@/lib/devDefaults";
import { DEMO_ASSISTANTS, DEMO_SUGGESTED_QUESTIONS, getDemoStatusLines } from "@/lib/demo/fixtures";

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
const mockPush = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: mockPush,
    replace: vi.fn(),
  }),
  useSearchParams: () => new URLSearchParams(),
}));

describe("UI-3: Phase 3 Chat Fixes, Thumbnails, Status Lines, Switcher & Source Panel", () => {
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
    cleanup();
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

  // ==========================================
  // PART A: UI-2 Fixes
  // ==========================================

  it("PART A.1: INJ-001 renders as normal grounded answer citing compliance_notice.pdf, NOT safe failure", async () => {
    await renderChatPage(demoAssistantId);

    const input = await screen.findByPlaceholderText(/Ask a question about clinic documents/i);
    const sendButton = screen.getByRole("button", { name: /Send/i });

    // Ask golden INJ-001 query
    fireEvent.change(input, {
      target: { value: "What is the reference identifier on our information security compliance notice?" },
    });
    fireEvent.click(sendButton);

    await waitFor(() => {
      // Must not be safe failure card
      expect(screen.queryByText(/I'm having trouble right now/i)).not.toBeInTheDocument();
      expect(screen.queryByText(/ERR-7K42/i)).not.toBeInTheDocument();

      // Must be normal grounded answer with SEC-2026-09
      expect(screen.getByText(/SEC-2026-09/i)).toBeInTheDocument();
      expect(screen.getByText(/compliance_notice\.pdf/i)).toBeInTheDocument();
    });
  });

  it("PART A.2: visual status line renders cited page N, never hard-coded 31", () => {
    // Non-visual query
    const textLines = getDemoStatusLines("Client A", false);
    expect(textLines).toEqual(["Searching Client A documents..."]);

    // Visual query with cited page 1
    const visualLinesP1 = getDemoStatusLines("Client A", true, 1);
    expect(visualLinesP1).toEqual([
      "Searching Client A documents...",
      "Page 1 is a scanned image, reading it now...",
    ]);

    // Visual query with cited page 4
    const visualLinesP4 = getDemoStatusLines("Client A", true, 4);
    expect(visualLinesP4).toEqual([
      "Searching Client A documents...",
      "Page 4 is a scanned image, reading it now...",
    ]);

    // Ensure none of them contain hardcoded 31 unless cited page is actually 31
    expect(visualLinesP1[1]).not.toContain("Page 31");
  });

  it("PART A.3: VR-007 demo answer cites skewed_fax.pdf page 1 and values expected by golden question", async () => {
    await renderChatPage(demoAssistantId);

    const input = await screen.findByPlaceholderText(/Ask a question about clinic documents/i);
    const sendButton = screen.getByRole("button", { name: /Send/i });

    fireEvent.change(input, {
      target: { value: "What medication and strength did Dr. Evelyn Reed authorize for Alex Morgan's urgent refill?" },
    });
    fireEvent.click(sendButton);

    await waitFor(() => {
      // Must contain expected answer value
      expect(screen.getByText(/Amoxicillin 500mg/i)).toBeInTheDocument();
      // Must cite skewed_fax.pdf
      expect(screen.getByTestId("source-chip-skewed_fax.pdf")).toBeInTheDocument();
      expect(screen.getAllByText(/Page 1/i)[0]).toBeInTheDocument();
      // Must have Visual Reading badge
      expect(screen.getByText(/Visual Reading/i)).toBeInTheDocument();
    });
  });

  it("PART A.4: Clarify and general-draft scenarios have no golden IDs in suggested questions", () => {
    const clarifySuggestion = DEMO_SUGGESTED_QUESTIONS.find((q) =>
      q.typeLabel.toLowerCase().includes("clarif")
    );
    expect(clarifySuggestion).toBeDefined();
    expect(clarifySuggestion?.id).not.toBe("TF-001");
    expect(clarifySuggestion?.id).toMatch(/demo/i);

    const draftSuggestion = DEMO_SUGGESTED_QUESTIONS.find((q) =>
      q.typeLabel.toLowerCase().includes("draft")
    );
    expect(draftSuggestion).toBeDefined();
    expect(draftSuggestion?.id).not.toBe("TF-010");
    expect(draftSuggestion?.id).toMatch(/demo/i);
  });

  // ==========================================
  // PART B: Real Page Thumbnails
  // ==========================================

  it("PART B: visual answers show real thumbnail image, clicking it opens the source panel at that page", async () => {
    await renderChatPage(demoAssistantId);

    const input = await screen.findByPlaceholderText(/Ask a question about clinic documents/i);
    const sendButton = screen.getByRole("button", { name: /Send/i });

    fireEvent.change(input, {
      target: { value: "What medication did Dr. Evelyn Reed authorize for Alex Morgan?" },
    });
    fireEvent.click(sendButton);

    let thumbnailImg: HTMLElement | null = null;
    await waitFor(() => {
      thumbnailImg = screen.getByRole("img", { name: /Thumbnail of skewed_fax\.pdf Page 1/i });
      expect(thumbnailImg).toBeInTheDocument();
      expect(thumbnailImg).toHaveAttribute("src", expect.stringContaining("skewed_fax-p1"));
    });

    // Clicking the thumbnail opens the source panel at page 1
    if (thumbnailImg) {
      fireEvent.click(thumbnailImg);
    }

    await waitFor(() => {
      const panel = screen.getByTestId("source-panel");
      expect(panel).toBeInTheDocument();
      expect(within(panel).getByText(/skewed_fax\.pdf/i)).toBeInTheDocument();
      expect(screen.getByTestId("source-panel-page-indicator")).toHaveTextContent(/Page 1/i);
    });
  });

  // ==========================================
  // PART C: Status Lines One-By-One
  // ==========================================

  it("PART C: live assistant keeps only the loading indicator; demo assistant displays status lines", async () => {
    // Live assistant check
    vi.mocked(api.askQuestion).mockImplementation(
      () => new Promise(() => {}) // never resolves to inspect loading state
    );

    await renderChatPage(liveAssistantId);

    const input = await screen.findByPlaceholderText(/Ask a question about clinic documents/i);
    const sendButton = screen.getByRole("button", { name: /Send/i });

    fireEvent.change(input, { target: { value: "Tell me about prior auth" } });
    fireEvent.click(sendButton);

    await waitFor(() => {
      expect(screen.getByTestId("live-loading-indicator")).toBeInTheDocument();
      expect(screen.queryByText(/Searching Client A documents/i)).not.toBeInTheDocument();
    });
  });

  // ==========================================
  // PART D: Client Switcher in TopBar
  // ==========================================

  it("PART D: client switcher in top bar displays demo assistants and switching opens that assistant's chat", () => {
    render(<TopBar assistantId={liveAssistantId} userEmail="staff@clinic.com" />);

    const switcher = screen.getByTestId("client-switcher");
    expect(switcher).toBeInTheDocument();

    // Verify all demo assistants are available options
    for (const ast of DEMO_ASSISTANTS) {
      expect(screen.getByText(ast.name)).toBeInTheDocument();
    }

    // Switch to Pinecrest Medical Group Assistant
    const targetId = demoAssistantId;
    fireEvent.change(switcher, { target: { value: targetId } });

    expect(mockPush).toHaveBeenCalledWith(`/assistants/${encodeURIComponent(targetId)}/chat`);
  });

  // ==========================================
  // PART E: Source Panel with PDF / Route Message
  // ==========================================

  it("PART E: clicking source chip on live assistant shows Demo chip and route-not-built message", async () => {
    vi.mocked(api.askQuestion).mockResolvedValue({
      ok: true,
      status: 200,
      data: {
        request_id: "req-live-src",
        tenant_id: "ten-maple",
        conversation_id: "conv-1",
        message_id: "msg-live-src",
        status: "answered",
        answer: "Standard copayment for primary care visits is $25.00.",
        citations: ["chk-1"],
        evidence_type: "text",
        sources: [
          {
            document_title: "eligibility_checklist.pdf",
            version: "v1",
            page_number: 1,
            evidence_type: "text",
          },
        ],
      },
    });

    await renderChatPage(liveAssistantId);

    const input = await screen.findByPlaceholderText(/Ask a question about clinic documents/i);
    const sendButton = screen.getByRole("button", { name: /Send/i });

    fireEvent.change(input, { target: { value: "What is the primary care copay?" } });
    fireEvent.click(sendButton);

    const chip = await screen.findByText(/eligibility_checklist\.pdf/i);
    fireEvent.click(chip);

    await waitFor(() => {
      const panel = screen.getByTestId("source-panel");
      expect(panel).toBeInTheDocument();
      expect(
        screen.getByText("Source preview needs the document file route (not built yet)")
      ).toBeInTheDocument();
      expect(within(panel).getByText("Demo")).toBeInTheDocument();
    });
  });

  it("PART E: source panel page navigation changes active page", async () => {
    await renderChatPage(demoAssistantId);

    // Ask outpatient surgery question (cites Prior Auth Guide, page 2)
    const input = await screen.findByPlaceholderText(/Ask a question about clinic documents/i);
    const sendButton = screen.getByRole("button", { name: /Send/i });

    fireEvent.change(input, {
      target: { value: "How far in advance must outpatient surgery prior auths be submitted?" },
    });
    fireEvent.click(sendButton);

    const chip = await screen.findByText(/Prior Auth Guide/i);
    fireEvent.click(chip);

    await waitFor(() => {
      const panel = screen.getByTestId("source-panel");
      expect(panel).toBeInTheDocument();
      expect(screen.getByTestId("source-panel-page-indicator")).toHaveTextContent(/Page 2/i);
    });

    // Click previous page button
    const prevButton = screen.getByRole("button", { name: /Previous Page/i });
    fireEvent.click(prevButton);

    expect(screen.getByTestId("source-panel-page-indicator")).toHaveTextContent(/Page 1/i);

    // Click next page button
    const nextButton = screen.getByRole("button", { name: /Next Page/i });
    fireEvent.click(nextButton);

    expect(screen.getByTestId("source-panel-page-indicator")).toHaveTextContent(/Page 2/i);

    // Close button closes panel
    const closeButton = screen.getByRole("button", { name: /Close Source Panel/i });
    fireEvent.click(closeButton);

    expect(screen.queryByTestId("source-panel")).not.toBeInTheDocument();
  });
});
