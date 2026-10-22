import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, act, within, cleanup } from "@testing-library/react";
import ChatPage from "@/app/assistants/[assistantId]/page";
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
    uploadSessionDocument: vi.fn(),
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

describe("Phase 4 Task 4.6: Live Extraction View, Side-by-Side Table & All 7 Field States", () => {
  const liveAssistantId = DEV_DEFAULT_ASSISTANT_ID;
  const originalEnv = process.env.NEXT_PUBLIC_DATA_SOURCE;

  const mockAll7StatesFields: ExtractedField[] = [
    {
      name: "patient_name",
      value: "Alex Morgan",
      read1_value: "Alex Morgan",
      read2_value: "Alex Morgan",
      state: "FILLED_HIGH",
      confidence: 0.98,
      page_number: 1,
      document_title: "skewed_fax.pdf",
    },
    {
      name: "authorization_date",
      value: "2026-02-14",
      read1_value: "2026-02-14",
      read2_value: null,
      state: "FILLED_MEDIUM",
      confidence: 0.82,
      page_number: 1,
      document_title: "skewed_fax.pdf",
    },
    {
      name: "authorized_medication",
      value: null,
      read1_value: "Amoxicillin 5oOmg",
      read2_value: "Amoxicillin 500mg",
      state: "CONFLICTING",
      confidence: 0.74,
      page_number: 1,
      document_title: "skewed_fax.pdf",
    },
    {
      name: "prescriber_signature",
      value: null,
      read1_value: null,
      read2_value: "[unreadable]",
      state: "UNREADABLE",
      confidence: 0.40,
      page_number: 1,
      document_title: "skewed_fax.pdf",
    },
    {
      name: "prescriber_npi",
      value: "1982736451",
      read1_value: "1982736451",
      read2_value: "1982736451",
      state: "INVALID",
      confidence: 0.99,
      validator_error: "CMS NPI Luhn check digit failure",
      page_number: 1,
      document_title: "skewed_fax.pdf",
    },
    {
      name: "secondary_insurance",
      value: null,
      read1_value: null,
      read2_value: null,
      state: "NOT_PRESENT",
      confidence: 0.90,
      page_number: 1,
      document_title: "skewed_fax.pdf",
    },
    {
      name: "emergency_override",
      value: null,
      read1_value: null,
      read2_value: null,
      state: "NOT_APPLICABLE",
      confidence: 0.95,
      page_number: 1,
      document_title: "skewed_fax.pdf",
    },
  ];

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

  it("attaching a fax file in live mode uploads session document and renders side-by-side extraction split view", async () => {
    vi.mocked(api.uploadSessionDocument).mockResolvedValue({
      ok: true,
      status: 200,
      data: {
        session_document_id: "doc-uuid-1",
        conversation_id: "conv-uuid-1",
        file_name: "skewed_fax.pdf",
        page_count: 1,
        status: "extracted",
        extracted_fields: mockAll7StatesFields,
        page_image_url: "/v1/assistants/00000000-0000-0000-0000-000000000101/conversations/conv-uuid-1/documents/doc-uuid-1/pages/1/image",
      },
    });

    await act(async () => {
      render(<ChatPage params={Promise.resolve({ assistantId: liveAssistantId })} />);
    });

    const fileInput = await screen.findByTestId("live-file-input");
    expect(fileInput).toBeDefined();

    const dummyFile = new File(["%PDF-1.4 test bytes"], "skewed_fax.pdf", { type: "application/pdf" });
    await act(async () => {
      fireEvent.change(fileInput, { target: { files: [dummyFile] } });
    });

    // 1. Verify uploadSessionDocument API call
    expect(api.uploadSessionDocument).toHaveBeenCalledTimes(1);

    // 2. Verify Invariant 7: Session document indicator
    const indicator = await screen.findByTestId("session-document-indicator");
    expect(indicator.textContent).toContain("Session document attached");
    expect(indicator.textContent).toContain("never added to the knowledge base");

    // 3. Verify side-by-side split view and page image
    const splitView = await screen.findByTestId("extraction-split-view");
    expect(splitView).toBeDefined();

    const pageImg = await screen.findByTestId("extraction-page-image");
    expect(pageImg.getAttribute("src")).toContain("/pages/1/image");

    // 4. Verify extraction table presence
    const table = await screen.findByTestId("extraction-field-table");
    expect(table).toBeDefined();
  });

  it("renders and styles all 7 explicit field states in the extraction table", async () => {
    vi.mocked(api.uploadSessionDocument).mockResolvedValue({
      ok: true,
      status: 200,
      data: {
        session_document_id: "doc-uuid-1",
        conversation_id: "conv-uuid-1",
        file_name: "skewed_fax.pdf",
        page_count: 1,
        status: "extracted",
        extracted_fields: mockAll7StatesFields,
        page_image_url: "/v1/assistants/00000000-0000-0000-0000-000000000101/conversations/conv-uuid-1/documents/doc-uuid-1/pages/1/image",
      },
    });

    await act(async () => {
      render(<ChatPage params={Promise.resolve({ assistantId: liveAssistantId })} />);
    });

    const fileInput = await screen.findByTestId("live-file-input");
    const dummyFile = new File(["%PDF-1.4 test"], "skewed_fax.pdf", { type: "application/pdf" });
    await act(async () => {
      fireEvent.change(fileInput, { target: { files: [dummyFile] } });
    });

    await screen.findByTestId("extraction-field-table");

    // State 1: FILLED_HIGH
    const rowHigh = screen.getByTestId("extraction-row-patient-name");
    expect(within(rowHigh).getByText("FILLED_HIGH")).toBeDefined();
    expect(within(rowHigh).getByText("Alex Morgan")).toBeDefined();

    // State 2: FILLED_MEDIUM
    const rowMed = screen.getByTestId("extraction-row-authorization-date");
    expect(within(rowMed).getByText("FILLED_MEDIUM")).toBeDefined();
    expect(within(rowMed).getByText("2026-02-14")).toBeDefined();

    // State 3: CONFLICTING (shows both readings, value is None)
    const rowConf = screen.getByTestId("extraction-row-authorized-medication");
    expect(within(rowConf).getByText("CONFLICTING")).toBeDefined();
    expect(within(rowConf).getByText(/Amoxicillin 5oOmg/)).toBeDefined();
    expect(within(rowConf).getByText(/Amoxicillin 500mg/)).toBeDefined();

    // State 4: UNREADABLE (Invariant 6: blank, flagged with ⚠️, never guessed)
    const rowUnreadable = screen.getByTestId("extraction-row-prescriber-signature");
    expect(within(rowUnreadable).getByText("UNREADABLE")).toBeDefined();
    expect(within(rowUnreadable).getByTestId("unreadable-flag")).toBeDefined();
    expect(within(rowUnreadable).getByText("[Blank - Unreadable]")).toBeDefined();

    // State 5: INVALID (shows format/check-digit error)
    const rowInvalid = screen.getByTestId("extraction-row-prescriber-npi");
    expect(within(rowInvalid).getByText("INVALID")).toBeDefined();
    expect(within(rowInvalid).getByText(/CMS NPI Luhn check digit failure/)).toBeDefined();

    // State 6: NOT_PRESENT
    const rowNotPresent = screen.getByTestId("extraction-row-secondary-insurance");
    expect(within(rowNotPresent).getByText("NOT_PRESENT")).toBeDefined();

    // State 7: NOT_APPLICABLE
    const rowNotApp = screen.getByTestId("extraction-row-emergency-override");
    expect(within(rowNotApp).getByText("NOT_APPLICABLE")).toBeDefined();
  });

  it("handles typed error response when session document upload fails", async () => {
    vi.mocked(api.uploadSessionDocument).mockResolvedValue({
      ok: false,
      status: 400,
      code: "INVALID_FILE_TYPE",
    });

    await act(async () => {
      render(<ChatPage params={Promise.resolve({ assistantId: liveAssistantId })} />);
    });

    const fileInput = await screen.findByTestId("live-file-input");
    const badFile = new File(["binary"], "bad.exe", { type: "application/x-dosexec" });
    await act(async () => {
      fireEvent.change(fileInput, { target: { files: [badFile] } });
    });

    const errorAlert = await screen.findByTestId("live-extraction-error");
    expect(errorAlert.textContent).toContain("INVALID_FILE_TYPE");
  });
});
