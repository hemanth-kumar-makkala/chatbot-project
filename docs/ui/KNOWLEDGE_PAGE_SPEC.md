# Knowledge Page UI Spec (v1)
## Purpose
Internal staff tool. A client admin uploads a client's documents, watches processing status, and reviews flagged pages. Not patient-facing. This is the only admin screen (no dashboards).
## Brand tokens (Staffingly Branding Guide)
- Primary blue #0037B2 (header, primary buttons, links)
- Primary gold #F5AF37 (main call-to-action)
- Gradient #0037B2 to #A941C6 (top bar, login page)
- Navy #293682 (headings, dark text)
- Purple #5F3DBF (selected/active states)
- Lavender #D8C7FA (needs-review chip background)
- Light blue #C2DCFF (info chip, "visual evidence" tag)
- Dark gold #BD7800 (warning text on light backgrounds)
- Cream #FFF8F2, light lilac #F8F2FF (page and card backgrounds)
- Font: "Barlow" if self-hosted later; for v1 use the stack: Barlow, "Segoe UI", system-ui, sans-serif. No external font or script CDNs.
All colours defined once as CSS variables. Text meets WCAG AA contrast.
## Screens
1. Login: email and password. Session is cookie-based; never store tokens in localStorage or sessionStorage.
2. Knowledge page at /assistants/[assistantId]/knowledge:
   - Top bar: gradient, "Staffingly" wordmark, assistant (client) name if the API provides it, user menu with logout.
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
