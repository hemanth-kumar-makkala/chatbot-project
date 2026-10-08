# ADR-0009: Shared Text Encoding Function in Ports Layer

## Status
Accepted

## Context
During Task 2.6 (Tabular Data Ingestion), both the upload validation service (`app/core/ingestion/validation.py`) and the spreadsheet table extractor adapter (`app/adapters/tables/builtin.py`) required identical text decoding behavior:
1. Decode raw file bytes as UTF-8.
2. Strip/handle UTF-8 Byte Order Mark (`\xef\xbb\xbf`) if present.
3. Fall back to Windows-1252 (`cp1252`) if UTF-8 decoding fails.
4. Reject corrupted binary data containing NUL bytes (`\x00`).
5. Raise `FileValidationError` on decode failure.

Our Hexagonal Architecture boundary rules (`tests/invariants/test_architecture_boundaries.py` and `AGENTS.md`) strictly enforce:
- `app/adapters/` may **never** import from `app/core/`.
- Only `app/wiring.py` may import from both `app.core` and `app.adapters`.

If `decode_text_content` lived solely in `app/core/ingestion/validation.py`, `BuiltinTableExtractor` could not use it without violating the architecture boundary. Duplicating the function in `app/adapters/` would risk subtle behavioral drift between file upload sniffing (Task 2.2) and spreadsheet parsing (Task 2.6).

## Decision
1. Place the canonical `decode_text_content` function in `app/ports/encoding.py`.
2. Both `app/core/` and `app/adapters/` import `decode_text_content` from `app.ports.encoding`. Core re-exports it for backward compatibility.
3. **Boundary Exception Policy**: As an architectural principle, `app/ports/` must otherwise contain strictly abstract interfaces (`abc.ABC`), error hierarchies (`PortError`), and data contracts/enums (`types.py`). `app/ports/encoding.py` is a narrow, deliberate exception because byte-to-string decoding is a protocol boundary contract with zero vendor dependencies (standard library only) and zero business domain logic.

## Consequences
- **Boundary Compliance**: Invariant test `test_repo_scan_app_adapters_clean` passes with zero violations.
- **Single Source of Truth**: Sniffing, CSV parsing, and upload validation use identical encoding detection and error handling.
- **Zero Vendor Contamination**: `app/ports/encoding.py` depends only on Python standard library and `app.ports.errors.FileValidationError`.
