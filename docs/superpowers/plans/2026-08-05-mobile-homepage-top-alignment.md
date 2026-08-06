# Mobile Homepage Top Alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the mobile initial-state search UI and its following discovery rows to the top of the homepage by replacing viewport-relative empty space with a compact fixed gap.

**Architecture:** Keep the existing React component hierarchy and normal document flow unchanged. Add a focused source-level CSS contract test, then change only the mobile `.search-initial-state` declaration in the shared stylesheet; the discovery rows move with it because they already follow `SearchSection` in `App`.

**Tech Stack:** React 19, TypeScript 7, Tailwind CSS 4, authored CSS, Vitest 4, Vite 8.

## Global Constraints

- Apply the layout change only below the existing `640px` mobile breakpoint.
- Use exactly `padding-top: 1rem` for the mobile initial-state search section.
- Preserve the existing Shelfmark title/logo, sticky header, safe-area handling, horizontal gutters, row spacing, and advanced-filter override.
- Do not change tablet or desktop layout, discovery behavior, data, ordering, loading, or interactions.
- Do not add dependencies.
- Preserve the untracked `.superpowers/` directory and `docs/superpowers/plans/2026-08-05-audible-topic-discovery.md`.

---

## File Structure

- Create `src/frontend/src/tests/mobileHomepageLayout.test.ts`: reads the authored stylesheet and protects the approved mobile top-spacing contract without requiring a browser-DOM dependency.
- Modify `src/frontend/src/styles.css`: changes the existing mobile `.search-initial-state` top padding from viewport-relative spacing to the approved fixed value.

### Task 1: Compact Mobile Initial-State Spacing

**Files:**
- Create: `src/frontend/src/tests/mobileHomepageLayout.test.ts`
- Modify: `src/frontend/src/styles.css:675-680`

**Interfaces:**
- Consumes: the existing `@media (max-width: 639px)` block and `.search-initial-state` selector.
- Produces: a CSS contract in which the mobile initial state declares `padding-top: 1rem` and contains no viewport-height top padding.

- [ ] **Step 1: Write the failing CSS contract test**

Create `src/frontend/src/tests/mobileHomepageLayout.test.ts`:

```typescript
import { readFileSync } from 'node:fs';

import { describe, expect, it } from 'vitest';

const styles = readFileSync(new URL('../styles.css', import.meta.url), 'utf8');

describe('mobile homepage layout', () => {
  it('keeps the initial search section near the top without viewport-height spacing', () => {
    const mobileInitialState = styles.match(
      /@media \(max-width: 639px\) \{\s*\.search-initial-state \{(?<declarations>[^}]*)\}/,
    );

    expect(mobileInitialState).not.toBeNull();
    expect(mobileInitialState?.groups?.declarations).toContain('padding-top: 1rem;');
    expect(mobileInitialState?.groups?.declarations).not.toMatch(/padding-top:\s*\d+(?:\.\d+)?vh/);
  });
});
```

- [ ] **Step 2: Run the focused test and verify the expected failure**

Run from `src/frontend`:

```bash
npm run test:unit -- src/tests/mobileHomepageLayout.test.ts
```

Expected: FAIL because the captured declarations contain `padding-top: 18vh;` rather than `padding-top: 1rem;`. The selector match itself must succeed.

- [ ] **Step 3: Implement the minimal CSS change**

In the existing mobile breakpoint in `src/frontend/src/styles.css`, change only the initial-state padding:

```css
@media (max-width: 639px) {
  .search-initial-state {
    justify-content: flex-start;
    padding-top: 1rem;
  }

  .search-initial-state.search-advanced-visible {
    padding-top: calc(env(safe-area-inset-top) + 1rem);
  }
}
```

Do not change the base desktop `.search-initial-state` rule or the advanced-filter override.

- [ ] **Step 4: Run the focused test and verify it passes**

Run:

```bash
npm run test:unit -- src/tests/mobileHomepageLayout.test.ts
```

Expected: PASS, 1 test passed and 0 failed.

- [ ] **Step 5: Run the complete frontend verification suite**

Run each command from `src/frontend` and require exit code 0:

```bash
npm run test:unit
npm run lint:warn
npm run format:check
npm run typecheck
npm run build
```

Expected: all unit tests pass, lint reports no warnings, formatting is clean, TypeScript reports no errors, and Vite completes a production build.

- [ ] **Step 6: Inspect the rendered mobile layout**

With the existing Shelfmark backend running on port `8084`, start the frontend from `src/frontend`:

```bash
npm run dev
```

Open `http://127.0.0.1:5173` at a `390 × 844` mobile viewport. Confirm:

- the sticky navigation and iOS safe-area spacing remain intact;
- the Shelfmark title begins approximately `1rem` below the main container's existing top padding;
- the search bar follows the title with its existing spacing;
- the first discovery row follows the search section without the former viewport-height gap;
- opening advanced filters still uses the safe-area-aware override;
- a desktop viewport retains the existing vertically offset initial search position.

Stop the development server after inspection.

- [ ] **Step 7: Review and commit the implementation**

Verify that only the intended test and stylesheet are part of the implementation diff:

```bash
git status --short
git diff --check
git diff -- src/frontend/src/styles.css src/frontend/src/tests/mobileHomepageLayout.test.ts
```

Commit only those files:

```bash
git add src/frontend/src/styles.css src/frontend/src/tests/mobileHomepageLayout.test.ts
git commit -m "fix(ui): align mobile homepage content to top"
```
