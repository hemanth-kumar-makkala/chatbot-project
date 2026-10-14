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
- Font: Barlow (SemiBold/Bold) for headings, system sans for body
All colours defined once as CSS variables. Text must meet WCAG AA contrast.

## Screens
1. Login: email and password, calls POST /v1/auth/login. Session is cookie-based; never store tokens in localStorage or sessionStorage.
2. Knowledge page at /assistants/[assistantId]/knowledge:
   - Top bar: gradient, "Staffingly" wordmark, assistant (client) name, user menu with logout.
   - Upload area: drag and drop or browse; PDF, XLSX, CSV; shows the 50 MB limit; one file at a time in v1; shows success, duplicate, or a friendly error.
   - Documents table: title, version, status chip, uploaded date, actions (Review, Retire with confirm dialog). Refresh status every 5 seconds while any document is not in a final state; stop when all are final.
   - Status chips: Queued, Reading, Scanning images, Ready (green), Ready (N pages need review) (lavender), Failed (red), Retired (grey).
3. Review drawer (opens from Review):
   - List of flagged pages: page number, reason as a plain label, page image (from the image route), reading text if any.
   - Actions per page: Accept, Exclude, Retry. Version action: Promote.
   - 409 "job active" shows: "This page is being processed. Try again in a moment." Other errors show a fixed friendly message plus the error code, never raw server text.

## Review reason labels
low_ocr_confidence = "Text unclear"; vision_failed = "Image read failed"; vision_low_confidence = "Image read uncertain"; vision_confidence_missing = "Image read not scored"; image_too_large = "Image too large"; processing_failed = "Processing failed"; legacy_unspecified = "Needs review (older upload)". Unknown values show "Needs review" and are reported as a UI bug, never hidden.

## Rules
- Every POST and DELETE sends the X-CSRF-Token header from the csrf_token cookie.
- No PHI or document text in console logs or analytics. No third-party scripts or fonts from external CDNs at runtime unless pinned and approved.
- Keyboard accessible: all actions reachable by Tab, visible focus, dialogs trap focus, Escape closes.
- Works at 1280px desktop width first; must not break at 768px.
