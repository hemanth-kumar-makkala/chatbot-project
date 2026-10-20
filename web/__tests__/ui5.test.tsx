import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, act, within, cleanup } from "@testing-library/react";
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
const mockPush = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: mockPush,
    replace: vi.fn(),
  }),
  useSearchParams: () => new URLSearchParams(),
}));

// Mock pdfjs-dist
const mockRenderTask = {
  promise: Promise.resolve(),
  cancel: vi.fn(),
};
const mockPage = {
  getViewport: vi.fn().mockReturnValue({ width: 600, height: 800 }),
  render: vi.fn().mockReturnValue(mockRenderTask),
};
const mockPdfDoc = {
  numPages: 4,
  getPage: vi.fn().mockResolvedValue(mockPage),
};
const mockGetDocument = vi.fn().mockImplementation(() => ({
  promise: Promise.resolve(mockPdfDoc),
}));

vi.mock("pdfjs-dist", () => ({
  getDocument: (src: string) => mockGetDocument(src),
  GlobalWorkerOptions: {
    workerSrc: "",
    workerPort: null,
  },
}));

describe("UI-5: Phase 5 UI - Feedback, Rate Limiting & Input Guards", () => {
  const liveAssistantId = DEV_DEFAULT_ASSISTANT_ID;
  const demoAssistantId = "00000000-0000-0000-0002-000000000002"; // Pinecrest Medical Group
  const originalEnv = process.env.NEXT_PUBLIC_DATA_SOURCE;

  beforeEach(() => {
    vi.clearAllMocks();
    cleanup();
    process.env.NEXT_PUBLIC_DATA_SOURCE = "live";
    vi.mocked(api.getCurrentUser).mockResolvedValue({
      ok: true,
      status: 200,
      data: {
        id: "user-1",
        email: "admin@clinic-a.local",
        name: "Clinic Administrator",
        tenant_id: "00000000-0000-0000-0000-000000000001",
      },
    });
  });

  afterEach(() => {
    cleanup();
    process.env.NEXT_PUBLIC_DATA_SOURCE = originalEnv;
  });

  describe("1. Feedback Controls (Task 5.5)", () => {
    it("assistant message contains thumbs up and thumbs down buttons", async () => {
      await act(async () => {
        render(<ChatPage params={Promise.resolve({ assistantId: demoAssistantId })} />);
      });

      // Submit a query
      const input = screen.getByRole("textbox");
      const sendBtn = screen.getByRole("button", { name: /Send/i });
      await act(async () => {
        fireEvent.change(input, { target: { value: "How far in advance must outpatient surgery prior auth be submitted?" } });
        fireEvent.click(sendBtn);
      });

      const thumbsUp = await screen.findByTestId("feedback-thumbs-up");
      const thumbsDown = await screen.findByTestId("feedback-thumbs-down");
      expect(thumbsUp).toBeDefined();
      expect(thumbsDown).toBeDefined();
    });

    it("live assistant thumbs up shows Demo chip and route not built message", async () => {
      vi.mocked(api.askQuestion).mockResolvedValue({
        ok: true,
        status: 200,
        data: {
          request_id: "req-live-1",
          tenant_id: "ten-live",
          conversation_id: "conv-live",
          message_id: "msg-live-1",
          status: "answered",
          answer: "Prior auth requests must be submitted at least 5 business days in advance.",
          citations: ["chunk-1"],
        },
      });

      await act(async () => {
        render(<ChatPage params={Promise.resolve({ assistantId: liveAssistantId })} />);
      });

      const input = screen.getByRole("textbox");
      const sendBtn = screen.getByRole("button", { name: /Send/i });
      await act(async () => {
        fireEvent.change(input, { target: { value: "When is prior auth due?" } });
        fireEvent.click(sendBtn);
      });

      const thumbsUp = await screen.findByTestId("feedback-thumbs-up");
      await act(async () => {
        fireEvent.click(thumbsUp);
      });

      const liveMsg = await screen.findByTestId("feedback-live-message");
      expect(liveMsg.textContent).toContain(
        "Feedback needs the feedback route (Phase 5 Task 5.5, not built yet)"
      );
      expect(within(liveMsg).getByText(/Demo/i)).toBeDefined();
    });

    it("demo assistant thumbs up records in memory with Demo chip and saved note", async () => {
      await act(async () => {
        render(<ChatPage params={Promise.resolve({ assistantId: demoAssistantId })} />);
      });

      const input = screen.getByRole("textbox");
      const sendBtn = screen.getByRole("button", { name: /Send/i });
      await act(async () => {
        fireEvent.change(input, { target: { value: "Emergency contact for Alex Morgan" } });
        fireEvent.click(sendBtn);
      });

      const thumbsUp = await screen.findByTestId("feedback-thumbs-up");
      await act(async () => {
        fireEvent.click(thumbsUp);
      });

      const demoNote = await screen.findByTestId("feedback-demo-saved-note");
      expect(demoNote.textContent).toContain("Saved for this demo session");
      expect(within(demoNote).getByText(/^Demo$/)).toBeDefined();
    });

    it("demo assistant thumbs down opens reason choices and text input, then saves", async () => {
      await act(async () => {
        render(<ChatPage params={Promise.resolve({ assistantId: demoAssistantId })} />);
      });

      const input = screen.getByRole("textbox");
      const sendBtn = screen.getByRole("button", { name: /Send/i });
      await act(async () => {
        fireEvent.change(input, { target: { value: "Emergency contact for Alex Morgan" } });
        fireEvent.click(sendBtn);
      });

      const thumbsDown = await screen.findByTestId("feedback-thumbs-down");
      await act(async () => {
        fireEvent.click(thumbsDown);
      });

      const popover = await screen.findByTestId("feedback-reason-popover");
      expect(popover).toBeDefined();

      // Check choice options
      expect(screen.getByLabelText(/Inaccurate facts/i)).toBeDefined();
      expect(screen.getByLabelText(/Too long/i)).toBeDefined();
      expect(screen.getByLabelText(/Wrong source/i)).toBeDefined();

      // Select a reason and type free text
      const inaccurateChoice = screen.getByLabelText(/Inaccurate facts/i);
      const commentInput = screen.getByTestId("feedback-comments-input");
      await act(async () => {
        fireEvent.click(inaccurateChoice);
        fireEvent.change(commentInput, { target: { value: "Phone number digits differ from patient card." } });
      });

      const submitReasonBtn = screen.getByTestId("feedback-submit-reason");
      await act(async () => {
        fireEvent.click(submitReasonBtn);
      });

      const demoNote = await screen.findByTestId("feedback-demo-saved-note");
      expect(demoNote.textContent).toContain("Saved for this demo session");
    });
  });

  describe("2. Rate Limit (Task 5.1)", () => {
    it("renders friendly wait message when question endpoint returns HTTP 429", async () => {
      vi.mocked(api.askQuestion).mockResolvedValue({
        ok: false,
        status: 429,
        code: "RATE_LIMIT_EXCEEDED",
      });

      await act(async () => {
        render(<ChatPage params={Promise.resolve({ assistantId: liveAssistantId })} />);
      });

      const input = screen.getByRole("textbox");
      const sendBtn = screen.getByRole("button", { name: /Send/i });
      await act(async () => {
        fireEvent.change(input, { target: { value: "Rapid repeated question" } });
        fireEvent.click(sendBtn);
      });

      // Assert friendly error without leaking raw server text
      const errorMsg = await screen.findByTestId("rate-limit-error-message");
      expect(errorMsg.textContent).toContain(
        "You've reached the query rate limit. Please wait a moment and try again."
      );
      expect(errorMsg.textContent).not.toContain("RATE_LIMIT_EXCEEDED");
      expect(errorMsg.textContent).not.toContain("429");
    });
  });
});
