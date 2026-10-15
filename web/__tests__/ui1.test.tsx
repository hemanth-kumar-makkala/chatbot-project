import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { Suspense } from "react";
import {
  getDataSourceMode,
  getFeatureDataSource,
  FEATURE_CONNECTIONS,
  REQUIRED_DATA_SOURCE_ERROR,
} from "@/lib/connections";
import { DataSourceChip } from "@/components/DataSourceChip";
import AssistantsPage from "@/app/assistants/page";
import ChatPage from "@/app/assistants/[assistantId]/chat/page";
import * as api from "@/lib/api";
import { DEV_DEFAULT_ASSISTANT_ID } from "@/lib/devDefaults";
import { DEMO_ASSISTANTS } from "@/lib/demo/fixtures";

// Mock API
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
const mockReplace = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: mockPush,
    replace: mockReplace,
  }),
  useSearchParams: () => new URLSearchParams(),
}));

describe("UI-1: Live/Demo Data Switch, Connections Map & UI Components", () => {
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

  describe("1. Live/Demo Switch & Startup Validation", () => {
    it("throws required error when NEXT_PUBLIC_DATA_SOURCE is missing or empty", () => {
      delete process.env.NEXT_PUBLIC_DATA_SOURCE;
      expect(() => getDataSourceMode()).toThrow(REQUIRED_DATA_SOURCE_ERROR);

      process.env.NEXT_PUBLIC_DATA_SOURCE = "";
      expect(() => getDataSourceMode()).toThrow(REQUIRED_DATA_SOURCE_ERROR);
    });

    it("throws required error when NEXT_PUBLIC_DATA_SOURCE is unknown", () => {
      process.env.NEXT_PUBLIC_DATA_SOURCE = "invalid_mode";
      expect(() => getDataSourceMode()).toThrow(REQUIRED_DATA_SOURCE_ERROR);

      process.env.NEXT_PUBLIC_DATA_SOURCE = "staging";
      expect(() => getDataSourceMode()).toThrow(REQUIRED_DATA_SOURCE_ERROR);
    });

    it("returns 'live' when NEXT_PUBLIC_DATA_SOURCE is 'live'", () => {
      process.env.NEXT_PUBLIC_DATA_SOURCE = "live";
      expect(getDataSourceMode()).toBe("live");
    });

    it("returns 'demo' when NEXT_PUBLIC_DATA_SOURCE is 'demo'", () => {
      process.env.NEXT_PUBLIC_DATA_SOURCE = "demo";
      expect(getDataSourceMode()).toBe("demo");
    });
  });

  describe("2. Connections Map (Matching CONNECTION_MAP.md)", () => {
    it("maps active backend features to 'live'", () => {
      expect(FEATURE_CONNECTIONS.auth_login).toBe("live");
      expect(FEATURE_CONNECTIONS.knowledge_list).toBe("live");
      expect(FEATURE_CONNECTIONS.knowledge_upload).toBe("live");
      expect(FEATURE_CONNECTIONS.knowledge_review).toBe("live");
      expect(FEATURE_CONNECTIONS.chat_ask).toBe("live");
      expect(FEATURE_CONNECTIONS.chat_citations).toBe("live");
    });

    it("maps unbuilt/missing backend features to 'demo'", () => {
      expect(FEATURE_CONNECTIONS.assistant_picker).toBe("demo");
      expect(FEATURE_CONNECTIONS.conversation_history).toBe("demo");
      expect(FEATURE_CONNECTIONS.streaming_status).toBe("demo");
      expect(FEATURE_CONNECTIONS.pdf_viewer).toBe("demo");
      expect(FEATURE_CONNECTIONS.refusal_presentation).toBe("demo");
      expect(FEATURE_CONNECTIONS.conflict_presentation).toBe("demo");
      expect(FEATURE_CONNECTIONS.clarify_presentation).toBe("demo");
      expect(FEATURE_CONNECTIONS.session_doc_upload).toBe("demo");
      expect(FEATURE_CONNECTIONS.extraction_table).toBe("demo");
      expect(FEATURE_CONNECTIONS.feedback_controls).toBe("demo");
    });

    it("resolves feature mode based on global setting", () => {
      process.env.NEXT_PUBLIC_DATA_SOURCE = "live";
      expect(getFeatureDataSource("chat_ask")).toBe("live");
      expect(getFeatureDataSource("assistant_picker")).toBe("demo");

      process.env.NEXT_PUBLIC_DATA_SOURCE = "demo";
      expect(getFeatureDataSource("chat_ask")).toBe("demo");
      expect(getFeatureDataSource("assistant_picker")).toBe("demo");
    });
  });

  describe("3. DataSourceChip Component", () => {
    it("renders Live chip with proper badge text", () => {
      render(<DataSourceChip mode="live" />);
      const chip = screen.getByText("Live");
      expect(chip).toBeDefined();
    });

    it("renders Demo chip with proper badge text", () => {
      render(<DataSourceChip mode="demo" />);
      const chip = screen.getByText("Demo");
      expect(chip).toBeDefined();
    });
  });

  describe("4. Assistant Picker Screen (/assistants)", () => {
    it("renders assistant list, demo chip, and handles assistant selection", async () => {
      await act(async () => {
        render(
          <Suspense fallback={<div>Loading...</div>}>
            <AssistantsPage />
          </Suspense>
        );
      });

      // Shows title and demo badge
      expect(screen.getByText("Select Client Assistant")).toBeDefined();
      expect(screen.getByText("Demo")).toBeDefined();

      // Displays golden-set assistant cards
      expect(screen.getByText("Maple Ridge Family Medicine Assistant")).toBeDefined();
      expect(screen.getByText("Pinecrest Medical Group Assistant")).toBeDefined();

      // Clicking an assistant navigates to its chat route
      const mapleBtn = screen.getByTestId(`assistant-select-${DEV_DEFAULT_ASSISTANT_ID}`);
      fireEvent.click(mapleBtn);
      expect(mockPush).toHaveBeenCalledWith(`/assistants/${DEV_DEFAULT_ASSISTANT_ID}/chat`);
    });
  });

  describe("5. Chat Layout with Conversation History", () => {
    it("renders conversation history list on left, chips, and new conversation button", async () => {
      await act(async () => {
        render(
          <Suspense fallback={<div>Loading...</div>}>
            <ChatPage params={Promise.resolve({ assistantId: DEV_DEFAULT_ASSISTANT_ID })} />
          </Suspense>
        );
      });

      // Check for left sidebar and conversation list
      expect(screen.getByText("Conversations")).toBeDefined();
      expect(screen.getByText("+ New Conversation")).toBeDefined();

      // Golden set conversations visible in sidebar
      expect(screen.getByText("Outpatient Surgery Prior Auth Submission Window")).toBeDefined();
      expect(screen.getByText("Intake Fax Policy Conflict (555-0101 vs 555-0102)")).toBeDefined();
      expect(screen.getByText("Patient Intake: Alex Morgan")).toBeDefined();

      // Shows both Demo chip for conversation history and Live chip for chat input/asking
      const chips = screen.getAllByTestId("data-source-chip");
      expect(chips.length).toBeGreaterThanOrEqual(2);

      // Clicking a conversation loads its messages
      const conflictConv = screen.getByText("Intake Fax Policy Conflict (555-0101 vs 555-0102)");
      fireEvent.click(conflictConv);

      const msgs = screen.getAllByText("What is the front desk intake fax line?");
      expect(msgs.length).toBe(2); // One in sidebar preview, one in chat message bubble
      expect(screen.getByText("Documents conflict on this point: Policy Version 1.0 lists front desk intake fax 555-0101, whereas Version 2.0 specifies revised intake fax 555-0102.")).toBeDefined();

      // Clicking + New Conversation resets the thread
      const newConvBtn = screen.getByText("+ New Conversation");
      fireEvent.click(newConvBtn);

      // In the message bubble area, only the sidebar preview remains (length 1)
      expect(screen.getAllByText("What is the front desk intake fax line?").length).toBe(1);
    });

    it("Maple Ridge fixture id equals DEV_DEFAULT_ASSISTANT_ID imported from devDefaults", () => {
      const mapleRidge = DEMO_ASSISTANTS.find((a) => a.client_name.includes("Maple Ridge"));
      expect(mapleRidge).toBeDefined();
      expect(mapleRidge?.id).toBe(DEV_DEFAULT_ASSISTANT_ID);
    });

    it("assistant matching DEV_DEFAULT_ASSISTANT_ID uses live query engine and calls askQuestion", async () => {
      vi.mocked(api.askQuestion).mockResolvedValueOnce({
        ok: true,
        status: 200,
        data: {
          request_id: "req-live-1",
          tenant_id: "ten-live",
          conversation_id: "conv-live-1",
          message_id: "msg-live-1",
          status: "answered",
          answer: "Live backend response from Maple Ridge assistant.",
          citations: [],
          evidence_type: "text",
          sources: [],
          conflict_sources: [],
        },
      });

      await act(async () => {
        render(
          <Suspense fallback={<div>Loading...</div>}>
            <ChatPage params={Promise.resolve({ assistantId: DEV_DEFAULT_ASSISTANT_ID })} />
          </Suspense>
        );
      });

      // Query engine header chip must be "Live"
      const queryEngineHeader = screen.getByText("Query Engine:").parentElement;
      expect(queryEngineHeader).toHaveTextContent("Live");

      // Submitting question invokes live backend askQuestion
      const input = screen.getByPlaceholderText(/Ask a question about clinic documents/i);
      fireEvent.change(input, { target: { value: "Live test question?" } });
      const sendBtn = screen.getByRole("button", { name: /send/i });
      await act(async () => {
        fireEvent.click(sendBtn);
      });

      expect(api.askQuestion).toHaveBeenCalledWith(
        DEV_DEFAULT_ASSISTANT_ID,
        "Live test question?",
        null
      );
      expect(screen.getByText("Live backend response from Maple Ridge assistant.")).toBeDefined();
    });

    it("demo assistant (non-default id) displays Demo chip and answers from demo fixtures without calling backend", async () => {
      const demoAssistantId = "00000000-0000-0000-0002-000000000002"; // Pinecrest Medical Group

      await act(async () => {
        render(
          <Suspense fallback={<div>Loading...</div>}>
            <ChatPage params={Promise.resolve({ assistantId: demoAssistantId })} />
          </Suspense>
        );
      });

      // Query engine header chip must be "Demo"
      const queryEngineHeader = screen.getByText("Query Engine:").parentElement;
      expect(queryEngineHeader).toHaveTextContent("Demo");

      // Submitting question MUST NOT call live askQuestion
      const input = screen.getByPlaceholderText(/Ask a question about clinic documents/i);
      fireEvent.change(input, { target: { value: "What is the emergency contact?" } });
      const sendBtn = screen.getByRole("button", { name: /send/i });
      await act(async () => {
        fireEvent.click(sendBtn);
      });

      expect(api.askQuestion).not.toHaveBeenCalled();
      // Demo fixture answer must be rendered
      expect(
        screen.getByText(
          "Emergency contact for Alex Morgan registered on file is phone 555-0131 (Apex Health Insurance)."
        )
      ).toBeDefined();
    });
  });
});
