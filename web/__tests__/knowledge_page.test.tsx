import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { Suspense } from "react";
import KnowledgePage from "@/app/assistants/[assistantId]/data-sources/page";
import * as api from "@/lib/api";

// Mock API methods
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    getCurrentUser: vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      data: { id: "usr_1", email: "admin@example.com", tenant_id: "ten_1" },
    }),
    getDocuments: vi.fn(),
    uploadDocument: vi.fn(),
    retireDocument: vi.fn(),
    getReviewData: vi.fn(),
    acceptPage: vi.fn(),
    excludePage: vi.fn(),
    retryPage: vi.fn(),
    promoteVersion: vi.fn(),
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

describe("Knowledge Page (Task 2.8b Step 5)", () => {
  const assistantId = "ast_test_123";

  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders documents with correct status chips", async () => {
    const mockDocs: api.DocumentItem[] = [
      {
        document_id: "doc_1",
        title: "Staff Policy 2026",
        current_version: 1,
        status: "queued",
        uploaded_at: "2026-10-10T10:00:00Z",
        latest_version_id: "ver_1",
        flagged_pages_count: 0,
      },
      {
        document_id: "doc_2",
        title: "Medical Benefits Guide",
        current_version: 2,
        status: "reading",
        uploaded_at: "2026-10-10T11:00:00Z",
        latest_version_id: "ver_2",
        flagged_pages_count: 0,
      },
      {
        document_id: "doc_3",
        title: "Clinic Handouts",
        current_version: 1,
        status: "scanning_images",
        uploaded_at: "2026-10-10T12:00:00Z",
        latest_version_id: "ver_3",
        flagged_pages_count: 0,
      },
      {
        document_id: "doc_4",
        title: "Standard Procedures",
        current_version: 3,
        status: "ready",
        uploaded_at: "2026-10-10T13:00:00Z",
        latest_version_id: "ver_4",
        flagged_pages_count: 0,
      },
      {
        document_id: "doc_5",
        title: "Complex Lab Fax",
        current_version: 1,
        status: "ready",
        uploaded_at: "2026-10-10T14:00:00Z",
        latest_version_id: "ver_5",
        flagged_pages_count: 3,
      },
      {
        document_id: "doc_6",
        title: "Corrupt File",
        current_version: 1,
        status: "failed",
        uploaded_at: "2026-10-10T15:00:00Z",
        latest_version_id: "ver_6",
        flagged_pages_count: 0,
      },
      {
        document_id: "doc_7",
        title: "Archived Handbook",
        current_version: 1,
        status: "retired",
        uploaded_at: "2026-10-10T16:00:00Z",
        latest_version_id: "ver_7",
        flagged_pages_count: 0,
      },
    ];

    vi.mocked(api.getDocuments).mockResolvedValue({
      ok: true,
      status: 200,
      data: mockDocs,
    });

    await act(async () => {
      render(
        <Suspense fallback={<div>Loading...</div>}>
          <KnowledgePage params={Promise.resolve({ assistantId })} />
        </Suspense>
      );
    });

    expect(await screen.findByText("Staff Policy 2026")).toBeInTheDocument();
    expect(screen.getByText("Medical Benefits Guide")).toBeInTheDocument();
    expect(screen.getByText("Clinic Handouts")).toBeInTheDocument();
    expect(screen.getByText("Standard Procedures")).toBeInTheDocument();
    expect(screen.getByText("Complex Lab Fax")).toBeInTheDocument();
    expect(screen.getByText("Corrupt File")).toBeInTheDocument();
    expect(screen.getByText("Archived Handbook")).toBeInTheDocument();

    // Verify chips
    expect(screen.getByText("Queued")).toBeInTheDocument();
    expect(screen.getByText("Reading")).toBeInTheDocument();
    expect(screen.getByText("Scanning images")).toBeInTheDocument();
    expect(screen.getByText("Ready")).toBeInTheDocument();
    expect(screen.getByText("Ready (3 pages need review)")).toBeInTheDocument();
    expect(screen.getByText("Failed")).toBeInTheDocument();
    expect(screen.getAllByText("Retired").length).toBeGreaterThan(0);
  });

  it("polling starts when documents are active, and stops when all are final", async () => {
    vi.useFakeTimers();

    const activeDoc: api.DocumentItem = {
      document_id: "doc_active",
      title: "In Progress Document",
      current_version: 1,
      status: "reading",
      uploaded_at: "2026-10-10T10:00:00Z",
      latest_version_id: "ver_1",
      flagged_pages_count: 0,
    };

    const finalDoc: api.DocumentItem = {
      ...activeDoc,
      status: "ready",
    };

    vi.mocked(api.getDocuments)
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        data: [activeDoc],
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        data: [finalDoc],
      });

    await act(async () => {
      render(<KnowledgePage params={Promise.resolve({ assistantId })} />);
    });

    expect(api.getDocuments).toHaveBeenCalledTimes(1);

    // Advance 5 seconds - polling should trigger since document was "reading"
    await act(async () => {
      vi.advanceTimersByTime(5000);
    });

    expect(api.getDocuments).toHaveBeenCalledTimes(2);

    // Advance another 5 seconds - document is now "ready" (final), polling must STOP
    await act(async () => {
      vi.advanceTimersByTime(5000);
    });

    // Should NOT call a third time because all documents are final
    expect(api.getDocuments).toHaveBeenCalledTimes(2);
  });

  it("upload rejects files over 50 MB and wrong types before sending", async () => {
    vi.mocked(api.getDocuments).mockResolvedValue({
      ok: true,
      status: 200,
      data: [],
    });

    await act(async () => {
      render(
        <Suspense fallback={<div>Loading...</div>}>
          <KnowledgePage params={Promise.resolve({ assistantId })} />
        </Suspense>
      );
    });

    const input = (await screen.findByTestId("upload-input")) as HTMLInputElement;

    // 1. File over 50 MB (e.g. 51 MB)
    const largeFile = new File([new Uint8Array(10)], "large.pdf", {
      type: "application/pdf",
    });
    Object.defineProperty(largeFile, "size", { value: 51 * 1024 * 1024 });

    await act(async () => {
      fireEvent.change(input, { target: { files: [largeFile] } });
    });

    expect(api.uploadDocument).not.toHaveBeenCalled();
    expect(screen.getByText("File exceeds 50 MB limit.")).toBeInTheDocument();

    // 2. Unsupported file extension/type (e.g. exe or png)
    const invalidFile = new File(["binary"], "badfile.exe", {
      type: "application/x-msdownload",
    });

    await act(async () => {
      fireEvent.change(input, { target: { files: [invalidFile] } });
    });

    expect(api.uploadDocument).not.toHaveBeenCalled();
    expect(screen.getByText("Invalid file type. Only PDF, XLSX, and CSV are supported.")).toBeInTheDocument();

    // 3. Valid file (PDF under 50 MB)
    const validFile = new File(["pdf-content"], "valid.pdf", {
      type: "application/pdf",
    });
    vi.mocked(api.uploadDocument).mockResolvedValue({
      ok: true,
      status: 200,
      data: {
        document_id: "doc_new",
        version_id: "ver_new",
        assistant_id: assistantId,
        filename: "valid.pdf",
        content_hash: "hash123",
        duplicate: false,
      },
    });

    await act(async () => {
      fireEvent.change(input, { target: { files: [validFile] } });
    });

    expect(api.uploadDocument).toHaveBeenCalledWith(assistantId, validFile);
    expect(await screen.findByText("Document uploaded successfully.")).toBeInTheDocument();
  });

  it("retire asks for confirmation and calls the right route", async () => {
    const docToRetire: api.DocumentItem = {
      document_id: "doc_retire_123",
      title: "Document To Retire",
      current_version: 1,
      status: "ready",
      uploaded_at: "2026-10-10T10:00:00Z",
      latest_version_id: "ver_123",
      flagged_pages_count: 0,
    };

    vi.mocked(api.getDocuments).mockResolvedValue({
      ok: true,
      status: 200,
      data: [docToRetire],
    });

    vi.mocked(api.retireDocument).mockResolvedValue({
      ok: true,
      status: 200,
      data: {
        ok: true,
        document_id: "doc_retire_123",
        status: "retired",
      },
    });

    await act(async () => {
      render(
        <Suspense fallback={<div>Loading...</div>}>
          <KnowledgePage params={Promise.resolve({ assistantId })} />
        </Suspense>
      );
    });

    const retireBtn = await screen.findByTestId("retire-btn-doc_retire_123");
    expect(retireBtn).toBeInTheDocument();

    // Click Retire button
    fireEvent.click(retireBtn);

    // Confirmation dialog should open with title and message
    const dialog = screen.getByRole("dialog");
    expect(screen.getByRole("heading", { name: "Retire Document" })).toBeInTheDocument();
    expect(dialog).toHaveTextContent(/Are you sure you want to retire/);
    expect(dialog).toHaveTextContent("Document To Retire");

    // Click confirm in dialog
    const confirmBtn = screen.getByRole("button", { name: "Retire Document" });
    await act(async () => {
      fireEvent.click(confirmBtn);
    });

    // Check retireDocument was called with assistantId and document_id
    expect(api.retireDocument).toHaveBeenCalledWith(assistantId, "doc_retire_123");
  });
});
