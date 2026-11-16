import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, act, within, cleanup } from "@testing-library/react";
import ChatPage from "@/app/assistants/[assistantId]/page";
import { ExtractionTable } from "@/components/ExtractionTable";
import * as api from "@/lib/api";
import { DEV_DEFAULT_ASSISTANT_ID } from "@/lib/devDefaults";
import type { ExtractedField } from "@/lib/api";
import {
  SKEWED_FAX_EXTRACTION_FIELDS,
  FAINT_FAX_EXTRACTION_FIELDS,
  CLEAN_FAX_EXTRACTION_FIELDS,
  INTAKE_FORM_EXTRACTION_FIELDS,
  GOLDEN_FAX_DOCUMENTS,
} from "@/lib/demo/fixtures";

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
    it("live assistant triggers live file input on attach button click without stale notice", async () => {
      await act(async () => {
        render(<ChatPage params={Promise.resolve({ assistantId: liveAssistantId })} />);
      });

      const attachBtn = await screen.findByTestId("chat-attach-button");
      expect(attachBtn).toBeDefined();

      const fileInput = await screen.findByTestId("live-file-input");
      const clickSpy = vi.spyOn(fileInput, "click");

      await act(async () => {
        fireEvent.click(attachBtn);
      });

      expect(clickSpy).toHaveBeenCalled();
      expect(screen.queryByTestId("session-upload-live-message")).not.toBeInTheDocument();
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
      // User-facing text never shows internal rule numbers (Part A.6)
      expect(sessionIndicator.textContent).not.toContain("(Invariant 7)");
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
        name: "Authorized Medication",
        value: null,
        read1_value: "Amoxicillin 500mg",
        read2_value: "Amoxillin 500mg",
        state: "CONFLICTING",
        page_number: 1,
        document_title: "skewed_fax.pdf",
      },
      {
        name: "Prescriber Signature Line",
        value: null,
        state: "UNREADABLE",
        page_number: 1,
        document_title: "skewed_fax.pdf",
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

    it("renders explicit default theme tokenized badge for all 7 states", () => {
      const handleOpen = vi.fn();
      render(<ExtractionTable fields={mockFields} onOpenSource={handleOpen} />);

      expect(screen.getByTestId("field-badge-FILLED_HIGH")).toBeDefined();
      expect(screen.getByTestId("field-badge-FILLED_MEDIUM")).toBeDefined();
      expect(screen.getByTestId("field-badge-CONFLICTING")).toBeDefined();
      expect(screen.getByTestId("field-badge-UNREADABLE")).toBeDefined();
      expect(screen.getByTestId("field-badge-INVALID")).toBeDefined();
      expect(screen.getByTestId("field-badge-NOT_APPLICABLE")).toBeDefined();

      // NOT_PRESENT fields are collapsed into one line by default; click to expand
      const toggle = screen.getByTestId("not-present-toggle");
      expect(toggle.textContent).toContain("not present in this document");
      fireEvent.click(toggle);
      expect(screen.getByTestId("field-badge-NOT_PRESENT")).toBeDefined();
    });

    it("unreadable field value is strictly blank with warning flag and never guessed (Invariant 6)", () => {
      const handleOpen = vi.fn();
      render(<ExtractionTable fields={mockFields} onOpenSource={handleOpen} />);

      const unreadableRow = screen.getByTestId("extraction-row-prescriber-signature-line");
      expect(unreadableRow).toBeDefined();

      const valueCell = within(unreadableRow).getByTestId("field-value-cell");
      // Value must be blank or display flagged blank marker, never a guessed string
      expect(valueCell.textContent).toContain("[Blank - Unreadable]");
      expect(within(unreadableRow).getByTestId("unreadable-flag")).toBeDefined();
    });

    it("conflicting field displays both readings labelled Read 1 (OCR) and Read 2 (vision)", () => {
      const handleOpen = vi.fn();
      render(<ExtractionTable fields={mockFields} onOpenSource={handleOpen} />);

      const conflictRow = screen.getByTestId("extraction-row-authorized-medication");
      expect(conflictRow).toBeDefined();

      const conflictValues = within(conflictRow).getByTestId("conflicting-readings");
      expect(conflictValues.textContent).toContain("Read 1 (OCR):");
      expect(conflictValues.textContent).toContain("Read 2 (vision):");
      expect(conflictValues.textContent).toContain("Amoxicillin 500mg");
      expect(conflictValues.textContent).toContain("Amoxillin 500mg");
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

      const unreadableRow = screen.getByTestId("extraction-row-prescriber-signature-line");
      const conflictRow = screen.getByTestId("extraction-row-authorized-medication");
      const highRow = screen.getByTestId("extraction-row-patient-name");

      expect(unreadableRow.getAttribute("data-highlighted")).toBe("true");
      expect(conflictRow.getAttribute("data-highlighted")).toBe("true");
      expect(highRow.getAttribute("data-highlighted")).toBe("false");
    });

    it("clicking a row opens source panel at the cited page", async () => {
      const handleOpen = vi.fn();
      render(<ExtractionTable fields={mockFields} onOpenSource={handleOpen} />);

      const unreadableRow = screen.getByTestId("extraction-row-prescriber-signature-line");
      fireEvent.click(unreadableRow);

      expect(handleOpen).toHaveBeenCalledWith("skewed_fax.pdf", 1);
    });

    it("every golden fax extraction field set cites strictly its own document and pages (Part A.2)", () => {
      // 1. Skewed fax: all 10 fields cite skewed_fax.pdf page 1; no faint_fax fields
      expect(SKEWED_FAX_EXTRACTION_FIELDS.length).toBeGreaterThan(0);
      for (const field of SKEWED_FAX_EXTRACTION_FIELDS) {
        expect(field.document_title).toBe("skewed_fax.pdf");
        expect(field.page_number).toBe(1);
      }

      // 2. Faint fax: all fields cite faint_fax_2page.pdf pages 1 or 2
      expect(FAINT_FAX_EXTRACTION_FIELDS.length).toBeGreaterThan(0);
      for (const field of FAINT_FAX_EXTRACTION_FIELDS) {
        expect(field.document_title).toBe("faint_fax_2page.pdf");
        expect([1, 2]).toContain(field.page_number);
      }

      // 3. Clean fax: all fields cite clean_fax.pdf page 1
      expect(CLEAN_FAX_EXTRACTION_FIELDS.length).toBeGreaterThan(0);
      for (const field of CLEAN_FAX_EXTRACTION_FIELDS) {
        expect(field.document_title).toBe("clean_fax.pdf");
        expect(field.page_number).toBe(1);
      }

      // 4. Patient intake form: all fields cite patient_intake_form.pdf page 1
      expect(INTAKE_FORM_EXTRACTION_FIELDS.length).toBeGreaterThan(0);
      for (const field of INTAKE_FORM_EXTRACTION_FIELDS) {
        expect(field.document_title).toBe("patient_intake_form.pdf");
        expect(field.page_number).toBe(1);
      }

      // Each golden fax has its own distinct field set
      expect(GOLDEN_FAX_DOCUMENTS.length).toBe(4);
    });

    it("invalid NPI 1982736451 truly fails the Luhn check digit with 80840 prefix (Part A.5)", () => {
      function computeNpiCheckDigit(base9: string): number {
        const full = "80840" + base9;
        let total = 0;
        const digits = full.split("").reverse().map(Number);
        for (let i = 0; i < digits.length; i++) {
          const digit = digits[i];
          if (i % 2 === 0) {
            const doubled = digit * 2;
            total += doubled < 10 ? doubled : doubled - 9;
          } else {
            total += digit;
          }
        }
        const units = total % 10;
        return (10 - units) % 10;
      }

      const base9 = "198273645";
      const validCheckDigit = computeNpiCheckDigit(base9);
      // Valid check digit for base 198273645 is 0
      expect(validCheckDigit).toBe(0);

      // Demo value has 1 as the 10th digit: 1982736451
      const invalidNpi = "1982736451";
      const actualCheckDigit = Number(invalidNpi[9]);
      expect(actualCheckDigit).toBe(1);
      expect(actualCheckDigit).not.toBe(validCheckDigit);
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
