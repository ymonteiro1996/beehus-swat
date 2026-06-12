# AGENT 2 — Frontend Implementation Plan

> Formalize the existing HTML/JS frontend into a consistent, maintainable structure.
> This agent works on `templates/` and `static/` files.
> Reference: `api_contract.json` for all endpoint schemas.

---

## Prerequisites

- Tailwind CSS (via CDN)
- No build step — vanilla JS
- All fetch calls point at `api_contract.json` endpoints

---

## Phase 1: Create Mock Data Layer

1. Create `static/mock_data.js` — a JS module that provides mock responses for every endpoint in `api_contract.json`
2. Create `static/api.js` — a thin fetch wrapper:
   ```js
   const API = {
     useMocks: false,  // toggle for development without backend
     async get(path, params) { ... },
     async post(path, body) { ... },
   };
   ```
3. When `useMocks = true`, return data from `mock_data.js` instead of calling the server
4. This allows frontend development without the backend running

## Phase 2: Shared Components

Extract repeated UI patterns from existing templates into reusable JS functions in `static/components.js`:

| Component | Used By | Pattern |
|-----------|---------|---------|
| `DateCards(container, cards, onSelect)` | conciliacao, validacao | Grid of clickable date cards with count |
| `CompanySelector(container, companies, onChange)` | conciliacao, validacao, impostos | Dropdown with company filter |
| `DataTable(container, {headers, rows, onRowClick})` | All pages | Sortable table with sticky header |
| `Modal(id, {title, onClose})` | conciliacao (diag, filegen, replicate) | Reusable modal shell |
| `Pill(label, value, colorClass)` | conciliacao (NAV, cash), diagnose | Info pill badge |
| `StatusBadge(status)` | diagnose steps | ok/warning/error badge |
| `formatters` | All pages | fmtMoney, fmtPct, fmtNum, escHtml |

## Phase 3: Standardize Templates

For each existing template, refactor to use shared components:

| Template | Key Changes |
|----------|-------------|
| `base.html` | No changes — sidebar is correct |
| `conciliacao.html` | Use `DateCards`, `CompanySelector`, `DataTable`, extract inline JS |
| `validacao_rentabilidades.html` | Use `DateCards`, `CompanySelector`, `DataTable` |
| `precos.html` | Use `DataTable` |
| `impostos.html` | Use `CompanySelector`, `DataTable` |

## Phase 4: CSS Standardization

1. Create `static/styles.css` with shared utility classes beyond Tailwind:
   - `.table-wrap` — max-height scroll container
   - `.date-card` + `.date-card.active` — card styling
   - `.anomaly-row` — red background for flagged rows
   - `.cols-collapsed .col-opt` — toggle columns
2. Move inline `<style>` blocks from templates into `static/styles.css`
3. Reference from `base.html`

## Phase 5: Consistency Audit

For each template, verify:

1. **All fetch URLs match `api_contract.json` paths exactly**
2. **All response destructuring matches contract response shapes**
3. **Error states handled** — loading spinner, error message, empty state
4. **ESC closes modals** — single global handler pattern
5. **Number formatting consistent** — same fmtMoney/fmtPct everywhere

## Phase 6: Mock Integration Test

1. Set `API.useMocks = true` in `static/api.js`
2. Open each page in browser
3. Verify all components render with mock data
4. Verify no JS console errors
5. Set `API.useMocks = false` and verify with real backend

---

## Files Created/Modified

| Action | File |
|--------|------|
| Create | `static/mock_data.js` |
| Create | `static/api.js` |
| Create | `static/components.js` |
| Create | `static/styles.css` |
| Modify | `templates/base.html` (add CSS/JS includes) |
| Modify | `templates/*.html` (refactor to use shared components) |

## Integration Point

All fetch calls use paths from `api_contract.json`. The backend (Agent 1) guarantees response shapes match. The mock layer allows frontend to work independently. On integration, just set `useMocks = false`.

---

## Page Priority Order

1. **conciliacao.html** — most complex, most features
2. **validacao_rentabilidades.html** — recently built, cleanest
3. **precos.html** — search + chart
4. **impostos.html** — simple table view
