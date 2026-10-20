# Archived Brand Theme (Staffingly Inc.)

> **Note**: This theme is archived and not in use. Nothing in the application imports these files or tokens. The active theme is the default theme (`web/styles/themes/default/tokens.css`).

## Brand Tokens (from original `docs/ui/KNOWLEDGE_PAGE_SPEC.md`)
- Primary blue `#0037B2` (header, primary buttons, links)
- Primary gold `#F5AF37` (main call-to-action)
- Gradient `#0037B2` to `#A941C6` (top bar, login page)
- Navy `#293682` (headings, dark text)
- Purple `#5F3DBF` (selected/active states)
- Lavender `#D8C7FA` (needs-review chip background)
- Light blue `#C2DCFF` (info chip, "visual evidence" tag)
- Dark gold `#BD7800` (warning text on light backgrounds)
- Cream `#FFF8F2`, light lilac `#F8F2FF` (page and card backgrounds)
- Font: "Barlow" if self-hosted later; for v1 use the stack: Barlow, "Segoe UI", system-ui, sans-serif. No external font or script CDNs.

## Where Each Token Was Used in the UI
| Token | Original CSS Variable | Prior UI Usage Location |
|---|---|---|
| Primary blue `#0037B2` | `--color-primary-blue` | TopBar links, login/chat send buttons, active link underlines, focus outlines |
| Primary gold `#F5AF37` | `--color-primary-gold` | Login CTA button, review promote button |
| Gradient `#0037B2` to `#A941C6` | `--gradient-brand` | TopBar background, login screen header background |
| Navy `#293682` | `--color-navy`, `--color-text-main` | Headings (`<h1>`, `<h2>`), body text, table headers, dark text, modal titles |
| Purple `#5F3DBF` | `--color-purple`, `--focus-outline-color` | Keyboard focus ring, active filter/toggle states |
| Lavender `#D8C7FA` | `--color-lavender`, `--color-status-review-bg` | "Ready (N pages need review)" status chip background, demo chips |
| Light blue `#C2DCFF` | `--color-light-blue`, `--color-status-in-progress-bg` | "Reading" / "In Progress" chip, visual evidence badge background |
| Dark gold `#BD7800` | `--color-dark-gold` | Medium confidence field badge text, warning callouts |
| Cream `#FFF8F2` | `--color-cream`, `--color-bg-page` | Page background color across all views |
| Light lilac `#F8F2FF` | `--color-light-lilac`, `--color-bg-subtle` | Subtle card background, row hover background, drawer subheaders |
| Barlow font stack | `--font-family` | Global font-family across entire web application |

## Archival File
- Token definitions: `web/styles/themes/staffingly-inc/tokens.css`
- Status: Archived. Not imported anywhere.
