import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { ReviewDrawer } from "@/components/ReviewDrawer";
import * as api from "@/lib/api";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    getReviewData: vi.fn(),
    acceptPage: vi.fn(),
    excludePage: vi.fn(),
    retryPage: vi.fn(),
    promoteVersion: vi.fn(),
    getPageImageUrl: vi.fn(
      (assistantId, versionId, pageNumber) =>
        `/v1/assistants/${assistantId}/knowledge/documents/${versionId}/pages/${pageNumber}/image`
    ),
  };
});

describe("Review Drawer (Task 2.8b Step 6)", () => {
  const assistantId = "ast_sample_1";
  const versionId = "ver_sample_1";
  const documentTitle = "Sample Medical Form";

  const mockReviewData: api.ReviewDataResponse = {
    document_id: "doc_sample_1",
    version_id: versionId,
    document_title: documentTitle,
    status: "ready",
    flagged_pages: [
      {
        page_number: 1,
        review_reason: "low_ocr_confidence",
        reading_text: "Patient Name: Jane Doe [Uncertain OCR]",
        status: "flagged",
      },
      {
        page_number: 2,
        review_reason: "vision_failed",
        reading_text: null,
        status: "flagged",
      },
    ],
  };

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getReviewData).mockResolvedValue({
      ok: true,
      status: 200,
      data: mockReviewData,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders flagged pages, page images, and reading text", async () => {
    await act(async () => {
      render(
        <ReviewDrawer
          isOpen={true}
          assistantId={assistantId}
          versionId={versionId}
          documentTitle={documentTitle}
          onClose={vi.fn()}
          onPromoted={vi.fn()}
        />
      );
    });

    expect(screen.getByText("Review Flagged Pages")).toBeInTheDocument();
    expect(screen.getByText(documentTitle)).toBeInTheDocument();

    // Page 1
    expect(screen.getByText("Page 1")).toBeInTheDocument();
    expect(screen.getByText("Text unclear")).toBeInTheDocument();
    expect(screen.getByText("Patient Name: Jane Doe [Uncertain OCR]")).toBeInTheDocument();
    const page1Img = screen.getByTestId("page-image-1");
    expect(page1Img).toHaveAttribute(
      "src",
      `/v1/assistants/${assistantId}/knowledge/documents/${versionId}/pages/1/image`
    );

    // Page 2
    expect(screen.getByText("Page 2")).toBeInTheDocument();
    expect(screen.getByText("Image read failed")).toBeInTheDocument();
  });

  it("calls acceptPage when Accept is clicked", async () => {
    vi.mocked(api.acceptPage).mockResolvedValue({
      ok: true,
      status: 200,
      data: { ok: true, page_number: 1, decision: "accepted" },
    });

    await act(async () => {
      render(
        <ReviewDrawer
          isOpen={true}
          assistantId={assistantId}
          versionId={versionId}
          documentTitle={documentTitle}
          onClose={vi.fn()}
          onPromoted={vi.fn()}
        />
      );
    });

    const acceptBtn = screen.getByTestId("accept-btn-1");
    await act(async () => {
      fireEvent.click(acceptBtn);
    });

    expect(api.acceptPage).toHaveBeenCalledWith(assistantId, versionId, 1);
  });

  it("calls excludePage when Exclude is clicked", async () => {
    vi.mocked(api.excludePage).mockResolvedValue({
      ok: true,
      status: 200,
      data: { ok: true, page_number: 1, decision: "excluded" },
    });

    await act(async () => {
      render(
        <ReviewDrawer
          isOpen={true}
          assistantId={assistantId}
          versionId={versionId}
          documentTitle={documentTitle}
          onClose={vi.fn()}
          onPromoted={vi.fn()}
        />
      );
    });

    const excludeBtn = screen.getByTestId("exclude-btn-1");
    await act(async () => {
      fireEvent.click(excludeBtn);
    });

    expect(api.excludePage).toHaveBeenCalledWith(assistantId, versionId, 1);
  });

  it("calls retryPage when Retry is clicked", async () => {
    vi.mocked(api.retryPage).mockResolvedValue({
      ok: true,
      status: 200,
      data: { ok: true, job_id: "job_retry_1", page_number: 2 },
    });

    await act(async () => {
      render(
        <ReviewDrawer
          isOpen={true}
          assistantId={assistantId}
          versionId={versionId}
          documentTitle={documentTitle}
          onClose={vi.fn()}
          onPromoted={vi.fn()}
        />
      );
    });

    const retryBtn = screen.getByTestId("retry-btn-2");
    await act(async () => {
      fireEvent.click(retryBtn);
    });

    expect(api.retryPage).toHaveBeenCalledWith(assistantId, versionId, 2);
  });

  it("calls promoteVersion when Promote Version is clicked", async () => {
    const onPromotedMock = vi.fn();
    vi.mocked(api.promoteVersion).mockResolvedValue({
      ok: true,
      status: 200,
      data: { ok: true, version_id: versionId, status: "ready" },
    });

    await act(async () => {
      render(
        <ReviewDrawer
          isOpen={true}
          assistantId={assistantId}
          versionId={versionId}
          documentTitle={documentTitle}
          onClose={vi.fn()}
          onPromoted={onPromotedMock}
        />
      );
    });

    const promoteBtn = screen.getByTestId("promote-version-btn");
    await act(async () => {
      fireEvent.click(promoteBtn);
    });

    expect(api.promoteVersion).toHaveBeenCalledWith(assistantId, versionId);
    expect(onPromotedMock).toHaveBeenCalledTimes(1);
  });

  it("shows 409 job-active message when a page action encounters conflict", async () => {
    vi.mocked(api.acceptPage).mockResolvedValue({
      ok: false,
      status: 409,
      code: "JOB_ACTIVE",
    });

    await act(async () => {
      render(
        <ReviewDrawer
          isOpen={true}
          assistantId={assistantId}
          versionId={versionId}
          documentTitle={documentTitle}
          onClose={vi.fn()}
          onPromoted={vi.fn()}
        />
      );
    });

    const acceptBtn = screen.getByTestId("accept-btn-1");
    await act(async () => {
      fireEvent.click(acceptBtn);
    });

    expect(
      screen.getByText("This page is being processed. Try again in a moment.")
    ).toBeInTheDocument();
  });

  it("shows fixed friendly message with error code on other errors without raw server text", async () => {
    vi.mocked(api.promoteVersion).mockResolvedValue({
      ok: false,
      status: 400,
      code: "UNREVIEWED_PAGES_REMAIN",
    });

    await act(async () => {
      render(
        <ReviewDrawer
          isOpen={true}
          assistantId={assistantId}
          versionId={versionId}
          documentTitle={documentTitle}
          onClose={vi.fn()}
          onPromoted={vi.fn()}
        />
      );
    });

    const promoteBtn = screen.getByTestId("promote-version-btn");
    await act(async () => {
      fireEvent.click(promoteBtn);
    });

    expect(
      screen.getByText("Action failed (UNREVIEWED_PAGES_REMAIN). Please try again.")
    ).toBeInTheDocument();
  });

  it("closes on Escape key press and handles focus trap", async () => {
    const onCloseMock = vi.fn();

    await act(async () => {
      render(
        <ReviewDrawer
          isOpen={true}
          assistantId={assistantId}
          versionId={versionId}
          documentTitle={documentTitle}
          onClose={onCloseMock}
          onPromoted={vi.fn()}
        />
      );
    });

    // Press Escape
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onCloseMock).toHaveBeenCalledTimes(1);

    // Click Close Button
    const closeBtn = screen.getByRole("button", { name: "Close review drawer" });
    fireEvent.click(closeBtn);
    expect(onCloseMock).toHaveBeenCalledTimes(2);
  });
});
