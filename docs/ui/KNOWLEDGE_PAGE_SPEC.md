# Knowledge Page UI Spec (v1)
## Purpose
Internal staff tool. A client admin uploads a client's documents, watches processing status, and reviews flagged pages. Not patient-facing. This is the only admin screen (no dashboards).
## Theme tokens (default theme tokens (web/styles/themes/default/tokens.css))
Archived theme reference: [Archived Brand Theme](staffingly-inc/BRAND_THEME.md).
- Background #FFFFFF, surface (cards, panels) #F6F8FA, subtle surface (hover, selected rows) #EEF2F5, border #DDE3EA
- Text primary #17212B, text secondary #556270, text on primary #FFFFFF
- Primary (buttons, links, active tab) #0B6E79, primary hover #095861, primary soft background #E3F2F3
- Info #2B5FB4 with soft background #E8EFFA (also the "visual evidence" tag)
- Success #1D7A4E with soft background #E5F4EC (Ready)
- Warning text #8A5300 with soft background #FFF3DC (needs review, conflicting)
- Danger #B42318 with soft background #FDECEA (failed, invalid, errors)
- Neutral chip #E9EDF1 with text #3E4A57 (retired, not applicable)
- Focus ring #0B6E79, 2px, visible on every focusable element
- Font stack: system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif. No external font or script CDNs.
All colours defined once as CSS variables in `web/styles/themes/default/tokens.css`. Text meets WCAG AA contrast.
## Screens
1. Login: email and password. Session is cookie-based; never store tokens in localStorage or sessionStorage.
2. Knowledge page at /assistants/[assistantId]/knowledge:
   - Top bar: white background, bottom border, app name in text primary, active tab in primary, user menu with logout. No gradients anywhere.
   - Upload area: drag and drop or browse; PDF, XLSX, CSV; shows the 50 MB limit; one file at a time; shows success, duplicate, or a friendly error.
   - Documents table: title, version, status chip, uploaded date, actions (Review, Retire with confirm dialog). Refresh every 5 seconds while any document is not in a final state; stop when all are final.
   - Status chips: Queued, Reading, Scanning images, Ready (green), Ready (N pages need review) (lavender), Failed (red), Retired (grey).
3. Review drawer (from Review): list of flagged pages with page number, reason label, page image (from the image route), reading text if any. Actions per page: Accept, Exclude, Retry. Version action: Promote. A 409 job-active error shows "This page is being processed. Try again in a moment." Other errors show a fixed friendly message plus the error code, never raw server text.
## Review reason labels
low_ocr_confidence "Text unclear"; vision_failed "Image read failed"; vision_low_confidence "Image read uncertain"; vision_confidence_missing "Image read not scored"; image_too_large "Image too large"; processing_failed "Processing failed"; legacy_unspecified "Needs review (older upload)". Unknown values show "Needs review" and log a UI warning with the code only.
## Rules
- Every POST and DELETE sends the X-CSRF-Token header with the csrf_token cookie value.
- No document text, file names or PHI in console logs.
- Keyboard accessible: every action reachable by Tab, visible focus, dialogs trap focus, Escape closes.
- Designed for 1280px; must not break at 768px.
