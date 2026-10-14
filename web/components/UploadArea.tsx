"use client";

import { useState, useRef, type DragEvent, type ChangeEvent } from "react";
import { uploadDocument } from "@/lib/api";

const MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024; // 50 MB
const ALLOWED_EXTENSIONS = [".pdf", ".xlsx", ".csv"];
const ALLOWED_MIME_TYPES = [
  "application/pdf",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "text/csv",
];

interface UploadAreaProps {
  assistantId: string;
  onUploadSuccess: () => void;
}

export function UploadArea({ assistantId, onUploadSuccess }: UploadAreaProps) {
  const [isDragging, setIsDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [message, setMessage] = useState<{ text: string; type: "success" | "duplicate" | "error" } | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  function validateFile(file: File): string | null {
    if (file.size > MAX_FILE_SIZE_BYTES) {
      return "File exceeds 50 MB limit.";
    }

    const lowerName = file.name.toLowerCase();
    const hasValidExt = ALLOWED_EXTENSIONS.some((ext) => lowerName.endsWith(ext));
    const hasValidMime = file.type ? ALLOWED_MIME_TYPES.includes(file.type) : true;

    if (!hasValidExt && !hasValidMime) {
      return "Invalid file type. Only PDF, XLSX, and CSV are supported.";
    }

    return null;
  }

  async function handleFile(file: File) {
    setMessage(null);

    const validationError = validateFile(file);
    if (validationError) {
      setMessage({ text: validationError, type: "error" });
      return;
    }

    setUploading(true);
    try {
      const result = await uploadDocument(assistantId, file);
      if (result.ok) {
        if (result.data.duplicate) {
          setMessage({
            text: "This document is a duplicate of an existing upload.",
            type: "duplicate",
          });
        } else {
          setMessage({
            text: "Document uploaded successfully.",
            type: "success",
          });
        }
        onUploadSuccess();
      } else {
        if (result.code === "FILE_TOO_LARGE" || result.status === 413) {
          setMessage({ text: "Upload failed: File exceeds 50 MB limit.", type: "error" });
        } else if (result.code === "INVALID_CONTENT_TYPE" || result.status === 422) {
          setMessage({
            text: "Upload failed: Only PDF, XLSX, and CSV files are allowed.",
            type: "error",
          });
        } else {
          setMessage({ text: `Upload failed (${result.code}). Please try again.`, type: "error" });
        }
      }
    } finally {
      setUploading(false);
      if (fileInputRef.current) {
        fileInputRef.current.value = "";
      }
    }
  }

  function onDragOver(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setIsDragging(true);
  }

  function onDragLeave(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setIsDragging(false);
  }

  function onDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setIsDragging(false);

    if (uploading) return;

    const files = e.dataTransfer.files;
    if (files.length > 0) {
      handleFile(files[0]);
    }
  }

  function onInputChange(e: ChangeEvent<HTMLInputElement>) {
    const files = e.target.files;
    if (files && files.length > 0) {
      handleFile(files[0]);
    }
  }

  return (
    <div style={{ marginBottom: "var(--spacing-8)" }}>
      <div
        data-testid="upload-dropzone"
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
        onClick={() => fileInputRef.current?.click()}
        tabIndex={0}
        role="button"
        aria-label="Upload document: drag and drop or browse"
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            fileInputRef.current?.click();
          }
        }}
        style={{
          border: `2px dashed ${isDragging ? "var(--color-purple)" : "var(--color-primary-blue)"}`,
          borderRadius: "var(--radius-lg)",
          backgroundColor: isDragging ? "var(--color-light-lilac)" : "var(--color-bg-card)",
          padding: "var(--spacing-8) var(--spacing-6)",
          textAlign: "center",
          cursor: uploading ? "wait" : "pointer",
          transition: "all 0.2s ease",
          boxShadow: isDragging ? "var(--shadow-md)" : "var(--shadow-sm)",
        }}
      >
        <input
          ref={fileInputRef}
          type="file"
          id="document-upload-input"
          data-testid="upload-input"
          accept=".pdf,.xlsx,.csv,application/pdf,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,text/csv"
          onChange={onInputChange}
          style={{ display: "none" }}
          disabled={uploading}
        />

        <div style={{ marginBottom: "var(--spacing-2)" }}>
          <span style={{ fontSize: "2rem", color: "var(--color-primary-blue)" }} aria-hidden="true">
            📄
          </span>
        </div>

        <p
          style={{
            fontSize: "var(--font-size-base)",
            fontWeight: 600,
            color: "var(--color-navy)",
            marginBottom: "var(--spacing-1)",
          }}
        >
          {uploading ? "Uploading document..." : "Drag and drop a document here, or browse"}
        </p>

        <p
          style={{
            fontSize: "var(--font-size-xs)",
            color: "var(--color-text-muted)",
          }}
        >
          Supported formats: PDF, XLSX, CSV • Max file size: 50 MB
        </p>
      </div>

      {message && (
        <div
          role="alert"
          data-testid="upload-feedback"
          style={{
            marginTop: "var(--spacing-3)",
            padding: "var(--spacing-3) var(--spacing-4)",
            borderRadius: "var(--radius-md)",
            fontSize: "var(--font-size-sm)",
            fontWeight: 500,
            backgroundColor:
              message.type === "success"
                ? "var(--color-status-success-bg)"
                : message.type === "duplicate"
                ? "var(--color-light-blue)"
                : "var(--color-status-error-bg)",
            color:
              message.type === "success"
                ? "var(--color-status-success-text)"
                : message.type === "duplicate"
                ? "var(--color-navy)"
                : "var(--color-status-error-text)",
            border: `1px solid ${
              message.type === "success"
                ? "rgba(27, 94, 32, 0.2)"
                : message.type === "duplicate"
                ? "rgba(0, 55, 178, 0.2)"
                : "rgba(183, 28, 28, 0.2)"
            }`,
          }}
        >
          {message.text}
        </div>
      )}
    </div>
  );
}
