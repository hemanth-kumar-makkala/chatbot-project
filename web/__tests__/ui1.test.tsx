import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, act, waitFor } from "@testing-library/react";
import { Suspense } from "react";
import {
  getDataSourceMode,
  getFeatureDataSource,
  FEATURE_CONNECTIONS,
  REQUIRED_DATA_SOURCE_ERROR,
} from "@/lib/connections";
import { DataSourceChip } from "@/components/DataSourceChip";
import AssistantsPage from "@/app/assistants/page";
import ChatPage from "@/app/assistants/[assistantId]/page";
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
  usePathname: () => "/assistants",
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
      render(<DataSourceChip mode="live" label="Live" />);
      const chip = screen.getByText("Live");
      expect(chip).toBeDefined();
    });

    it("renders Demo chip with proper badge text", () => {
      render(<DataSourceChip mode="demo" label="Demo" />);
      const chip = screen.getByText("Demo");
      expect(chip).toBeDefined();
    });
  });

  describe("4. Assistant Picker Screen (/assistants)", () => {
    it("renders assistant list, demo chip, and handles assistant selection", async () => {
      process.env.NEXT_PUBLIC_DATA_SOURCE = "demo";
      await act(async () => {
        render(
          <Suspense fallback={<div>Loading...</div>}>
            <AssistantsPage />
          </Suspense>
        );
      });

      // Shows title and connection badge
      expect(screen.getByRole("heading", { name: "Assistants", level: 1 })).toBeDefined();
      expect(screen.getByText("Connected")).toBeDefined();

      // Displays golden-set assistant cards in demo mode
      expect(screen.getByText("Maple Ridge Family Medicine Assistant")).toBeDefined();
      expect(screen.getByText("Pinecrest Medical Group Assistant")).toBeDefined();

      // Clicking an assistant navigates to its route
      const mapleBtn = screen.getByTestId(`assistant-select-${DEV_DEFAULT_ASSISTANT_ID}`);
      fireEvent.click(mapleBtn);
      expect(mockPush).toHaveBeenCalledWith(`/assistants/${DEV_DEFAULT_ASSISTANT_ID}`);
    });
  });

  describe("5. Chat Layout with Assistant Shell", () => {
    it("renders sidebar with Conversations link and + New chat button", async () => {
      await act(async () => {
        render(
          <Suspense fallback={<div>Loading...</div>}>
            <ChatPage params={Promise.resolve({ assistantId: DEV_DEFAULT_ASSISTANT_ID })} />
          </Suspense>
        );
      });

      // Check for sidebar navigation and Conversations
      expect(screen.getByTestId("chat-sidebar")).toBeDefined();
      expect(screen.getByText("Conversations")).toBeDefined();
      expect(screen.getByRole("button", { name: /\+ new chat/i })).toBeDefined();
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
        },
      });

      await act(async () => {
        render(
          <Suspense fallback={<div>Loading...</div>}>
            <ChatPage params={Promise.resolve({ assistantId: DEV_DEFAULT_ASSISTANT_ID })} />
          </Suspense>
        );
      });

      const input = await screen.findByPlaceholderText(/Ask a question about clinic documents/i);
      const sendButton = screen.getByRole("button", { name: /Send/i });

      fireEvent.change(input, { target: { value: "Where is the clinic located?" } });
      fireEvent.click(sendButton);

      await waitFor(() => {
        expect(api.askQuestion).toHaveBeenCalledWith(
          DEV_DEFAULT_ASSISTANT_ID,
          "Where is the clinic located?",
          null
        );
      });
    });

    it("demo assistant (non-default id) displays answers from demo fixtures without calling backend", async () => {
      process.env.NEXT_PUBLIC_DATA_SOURCE = "demo";
      const demoId = "00000000-0000-0000-0002-000000000002"; // Pinecrest
      await act(async () => {
        render(
          <Suspense fallback={<div>Loading...</div>}>
            <ChatPage params={Promise.resolve({ assistantId: demoId })} />
          </Suspense>
        );
      });

      const input = await screen.findByPlaceholderText(/Ask a question about clinic documents/i);
      const sendButton = screen.getByRole("button", { name: /Send/i });

      fireEvent.change(input, {
        target: {
          value:
            "How far in advance must outpatient surgery prior auth be submitted?",
        },
      });
      fireEvent.click(sendButton);

      await waitFor(() => {
        expect(api.askQuestion).not.toHaveBeenCalled();
        expect(
          screen.getByText(
            /Prior authorization requests for outpatient surgery require submission/i
          )
        ).toBeInTheDocument();
      });
    });
  });
});
