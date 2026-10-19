import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, act, within, cleanup } from "@testing-library/react";
import ChatPage from "@/app/assistants/[assistantId]/chat/page";
import { ExtractionTable } from "@/components/ExtractionTable";
import * as api from "@/lib/api";
import { DEV_DEFAULT_ASSISTANT_ID } from "@/lib/devDefaults";
import type { ExtractedField } from "@/lib/api";

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

describe("UI-4: Phase 4 UI - Session Upload, Extraction View, Field States & Invariants 6 & 7", () => {
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
        email: "staff@client.example",
        name: "Staff Member",
        tenant_id: "00000000-0000-0000-0000-000000000001",
      },
    });
  });

  afterEach(() => {
    cleanup();
    process.env.NEXT_PUBLIC_DATA_SOURCE = originalEnv;
  });

  describe("1. Session Upload in Chat (Task 4.3, Invariant 7)", () => {
    it("live assistant shows Demo chip and route not built message on attach button click", async () => {
      await act(async () => {
        render(<ChatPage params={Promise.resolve({ assistantId: liveAssistantId })} />);
      });

      const attachBtn = await screen.findByTestId("chat-attach-button");
      expect(attachBtn).toBeDefined();

      await act(async () => {
        fireEvent.click(attachBtn);
      });

      const liveMessage = await screen.findByTestId("session-upload-live-message");
      expect(liveMessage.textContent).toContain(
        "Session upload needs the session document route (Phase 4 Task 4.3, not built yet)"
      );

      // Verify Demo chip presence within the message container
      const demoChip = within(liveMessage).getByText(/Demo/i);
      expect(demoChip).toBeDefined();
    });

    it("demo assistant offers golden fax document picker on attach button click", async () => {
      await act(async () => {
        render(<ChatPage params={Promise.resolve({ assistantId: demoAssistantId })} />);
      });

      const attachBtn = await screen.findByTestId("chat-attach-button");
      await act(async () => {
        fireEvent.click(attachBtn);
      });

      const picker = await screen.findByTestId("demo-attach-picker");
      expect(picker).toBeDefined();

      // Check golden fax options
      expect(screen.getByText("skewed_fax.pdf")).toBeDefined();
      expect(screen.getByText("clean_fax.pdf")).toBeDefined();
      expect(screen.getByText("faint_fax_2page.pdf")).toBeDefined();
      expect(screen.getByText("patient_intake_form.pdf")).toBeDefined();
    });

    it("demo assistant rejects arbitrary local file upload with fixed message", async () => {
      await act(async () => {
        render(<ChatPage params={Promise.resolve({ assistantId: demoAssistantId })} />);
      });

      const attachBtn = await screen.findByTestId("chat-attach-button");
      await act(async () => {
        fireEvent.click(attachBtn);
      });

      const fileInput = await screen.findByTestId("arbitrary-file-input");
      const arbitraryFile = new File(["arbitrary content"], "my_random_records.pdf", {
        type: "application/pdf",
      });

      await act(async () => {
        fireEvent.change(fileInput, { target: { files: [arbitraryFile] } });
      });

      const errorMsg = await screen.findByTestId("demo-arbitrary-file-error");
      expect(errorMsg.textContent).toContain(
        "Demo only reads sample fax documents from the golden set"
      );
    });

    it("selecting a golden fax displays session indicator with Invariant 7 guarantee", async () => {
      await act(async () => {
        render(<ChatPage params={Promise.resolve({ assistantId: demoAssistantId })} />);
      });

      const attachBtn = await screen.findByTestId("chat-attach-button");
      await act(async () => {
        fireEvent.click(attachBtn);
      });

      // Select skewed_fax.pdf from golden picker
      const skewedOption = screen.getByTestId("picker-doc-skewed_fax.pdf");
      await act(async () => {
        fireEvent.click(skewedOption);
      });

      // Session indicator must be visible above input
      const sessionIndicator = await screen.findByTestId("session-document-indicator");
      expect(sessionIndicator).toBeDefined();
      expect(sessionIndicator.textContent).toContain("skewed_fax.pdf");
      expect(sessionIndicator.textContent).toContain(
        "stays in this conversation only and is never added to the knowledge base"
      );
    });
  });

  describe("2. Extraction View & Status Lines (Staff day step 6, Task 4.6)", () => {
    it("renders dynamic status line and side-by-side extraction table beside page preview", async () => {
      await act(async () => {
        render(<ChatPage params={Promise.resolve({ assistantId: demoAssistantId })} />);
      });

      // Attach skewed_fax.pdf
      const attachBtn = await screen.findByTestId("chat-attach-button");
      await act(async () => {
        fireEvent.click(attachBtn);
      });
      const skewedOption = screen.getByTestId("picker-doc-skewed_fax.pdf");
      await act(async () => {
        fireEvent.click(skewedOption);
      });

      // Send extraction prompt
      const input = screen.getByRole("textbox");
      const sendBtn = screen.getByRole("button", { name: /Send/i });
      await act(async () => {
        fireEvent.change(input, { target: { value: "Upload patient fax and extract all fields" } });
        fireEvent.click(sendBtn);
      });

      // Status line check
      await waitFor(() => {
        const statusLines = screen.queryAllByTestId("extraction-status-line");
        const found = statusLines.some((el) =>
          el.textContent?.includes("Page 1 is a scanned image, reading it now...")
        );
        expect(found).toBe(true);
      });

      // Split view check
      const splitView = await screen.findByTestId("extraction-split-view");
      expect(splitView).toBeDefined();

      const pageImg = await screen.findByTestId("extraction-page-image");
      expect(pageImg.getAttribute("src")).toContain("skewed_fax-p1.png");

      const table = await screen.findByTestId("extraction-field-table");
      expect(table).toBeDefined();
    });
  });

  describe("3. Field States & Badges (Task 4.5, Invariant 6)", () => {
    const mockFields: ExtractedField[] = [
      {
        name: "Patient Name",
        value: "Alex Morgan",
        state: "FILLED_HIGH",
        confidence: 0.98,
        page_number: 1,
        document_title: "skewed_fax.pdf",
      },
      {
        name: "Chart Number",
        value: "MRN-4412-A",
        state: "FILLED_MEDIUM",
        confidence: 0.82,
        page_number: 1,
        document_title: "skewed_fax.pdf",
      },
      {
        name: "Intake Fax Number",
        value: null,
        read1_value: "555-0101",
        read2_value: "555-0102",
        state: "CONFLICTING",
        page_number: 1,
        document_title: "skewed_fax.pdf",
      },
      {
        name: "Clinical Justification Note",
        value: null,
        state: "UNREADABLE",
        page_number: 2,
        document_title: "faint_fax_2page.pdf",
      },
      {
        name: "Prescriber NPI",
        value: "1982736451",
        state: "INVALID",
        validator_error: "NPI check digit Luhn algorithm failed",
        page_number: 1,
        document_title: "skewed_fax.pdf",
      },
      {
        name: "Secondary Insurance Group",
        value: null,
        state: "NOT_PRESENT",
        page_number: 1,
        document_title: "skewed_fax.pdf",
      },
      {
        name: "Prior Auth Emergency Override",
        value: null,
        state: "NOT_APPLICABLE",
        page_number: 1,
        document_title: "skewed_fax.pdf",
      },
    ];

    it("renders explicit Staffingly tokenized badge for all 7 states", () => {
      const handleOpen = vi.fn();
      render(<ExtractionTable fields={mockFields} onOpenSource={handleOpen} />);

      expect(screen.getByTestId("field-badge-FILLED_HIGH")).toBeDefined();
      expect(screen.getByTestId("field-badge-FILLED_MEDIUM")).toBeDefined();
      expect(screen.getByTestId("field-badge-CONFLICTING")).toBeDefined();
      expect(screen.getByTestId("field-badge-UNREADABLE")).toBeDefined();
      expect(screen.getByTestId("field-badge-INVALID")).toBeDefined();
      expect(screen.getByTestId("field-badge-NOT_PRESENT")).toBeDefined();
      expect(screen.getByTestId("field-badge-NOT_APPLICABLE")).toBeDefined();
    });

    it("unreadable field value is strictly blank with warning flag and never guessed (Invariant 6)", () => {
      const handleOpen = vi.fn();
      render(<ExtractionTable fields={mockFields} onOpenSource={handleOpen} />);

      const unreadableRow = screen.getByTestId("extraction-row-clinical-justification-note");
      expect(unreadableRow).toBeDefined();

      const valueCell = within(unreadableRow).getByTestId("field-value-cell");
      // Value must be blank or display flagged blank marker, never a guessed string
      expect(valueCell.textContent).toContain("[Blank - Unreadable]");
      expect(within(unreadableRow).getByTestId("unreadable-flag")).toBeDefined();
    });

    it("conflicting field displays both readings side by side", () => {
      const handleOpen = vi.fn();
      render(<ExtractionTable fields={mockFields} onOpenSource={handleOpen} />);

      const conflictRow = screen.getByTestId("extraction-row-intake-fax-number");
      expect(conflictRow).toBeDefined();

      const conflictValues = within(conflictRow).getByTestId("conflicting-readings");
      expect(conflictValues.textContent).toContain("555-0101");
      expect(conflictValues.textContent).toContain("555-0102");
    });

    it("invalid field displays validator failure reason", () => {
      const handleOpen = vi.fn();
      render(<ExtractionTable fields={mockFields} onOpenSource={handleOpen} />);

      const invalidRow = screen.getByTestId("extraction-row-prescriber-npi");
      expect(within(invalidRow).getByText(/Luhn algorithm failed/i)).toBeDefined();
    });

    it("unreadable and conflicting rows are highlighted", () => {
      const handleOpen = vi.fn();
      render(<ExtractionTable fields={mockFields} onOpenSource={handleOpen} />);

      const unreadableRow = screen.getByTestId("extraction-row-clinical-justification-note");
      const conflictRow = screen.getByTestId("extraction-row-intake-fax-number");
      const highRow = screen.getByTestId("extraction-row-patient-name");

      expect(unreadableRow.getAttribute("data-highlighted")).toBe("true");
      expect(conflictRow.getAttribute("data-highlighted")).toBe("true");
      expect(highRow.getAttribute("data-highlighted")).toBe("false");
    });

    it("clicking a row opens source panel at the cited page", async () => {
      const handleOpen = vi.fn();
      render(<ExtractionTable fields={mockFields} onOpenSource={handleOpen} />);

      const unreadableRow = screen.getByTestId("extraction-row-clinical-justification-note");
      fireEvent.click(unreadableRow);

      expect(handleOpen).toHaveBeenCalledWith("faint_fax_2page.pdf", 2);
    });

    it("unknown field state renders fixed safe label and logs code only", () => {
      const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
      const unknownField: ExtractedField = {
        name: "Experimental Code",
        value: "EXP-99",
        state: "CORRUPTED_EXTRACTION_STATE",
        page_number: 1,
        document_title: "skewed_fax.pdf",
      };

      render(<ExtractionTable fields={[unknownField]} onOpenSource={vi.fn()} />);

      const row = screen.getByTestId("extraction-row-experimental-code");
      const badge = within(row).getByTestId("field-badge-UNKNOWN");
      expect(badge.textContent).toBe("UNKNOWN");

      expect(warnSpy).toHaveBeenCalledWith(
        "Unknown extraction field state:",
        "CORRUPTED_EXTRACTION_STATE"
      );
      warnSpy.mockRestore();
    });
  });
});
