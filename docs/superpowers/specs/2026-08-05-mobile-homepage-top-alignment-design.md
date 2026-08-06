# Mobile Homepage Top Alignment Design

## Context

On narrow screens, the initial homepage search section uses `padding-top: 18vh`. The viewport-relative spacing creates a large empty area between the top navigation and the Shelfmark title, search bar, and discovery rows. On the supplied iPhone screenshot, that gap is roughly 230 pixels.

## Approved Design

Replace the mobile initial-state viewport-relative top padding with a compact fixed `1rem` top padding. The existing main-container padding and header safe-area handling remain responsible for separation from the sticky navigation and iOS status area.

The change applies only below the existing `640px` mobile breakpoint. Tablet and desktop positioning remain unchanged. Opening advanced filters retains its existing safe-area-aware top padding.

Because the discovery section follows the search section in normal document flow, moving the search section upward also moves every discovery row upward without introducing independent offsets or positioning.

## Scope

- Change only the mobile initial-state search-section top padding.
- Preserve the Shelfmark title and logo above the search bar.
- Preserve existing horizontal gutters, row spacing, sticky navigation, and safe-area behavior.
- Do not alter tablet or desktop layout.
- Do not change discovery data, ordering, loading, or interaction behavior.

## Verification

- Add a focused CSS contract test that fails while mobile initial-state spacing uses viewport-height units and passes when it uses the approved fixed `1rem` value.
- Run the focused test before and after the production change to demonstrate red-green behavior.
- Run the frontend unit suite, lint, formatting check, typecheck, and production build.
- Inspect the rendered mobile layout to confirm the title, search bar, and first discovery row begin directly below the navigation with a compact gap and no safe-area overlap.
