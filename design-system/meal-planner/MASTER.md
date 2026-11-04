# Design System Master File

> **Logic:** when building a page, first check `design-system/meal-planner/pages/[page-name].md`.
> If that file exists, its rules override this file. If not, follow the rules below.
>
> The tokens live in `frontend/src/styles/tokens.css`. Build from them, never from ad-hoc values.

---

**Project:** Meal planner
**Created:** 2026-11-04. It began from a stock palette, type and spacing for a nutrition-tracking productivity tool, and was then edited by hand against the Avoid list below. What changed from that starting point, and why, is in the last section.
**Category:** Productivity tool
**Aim:** centered and minimal, almost no motion, standard density

---

## Direction: a calm tool for every day

People open this to cook from and to note what they ate. They read it on a phone propped against a jar in the kitchen, and at a desk once a week when they write the next week. So it is plain: text, tables of numbers that line up, rules and whitespace between groups, generous targets for wet thumbs, and one colour that means "you can act on this". Nothing moves unless the person did something. Status is said in words, and colour only repeats it.

**Style:** Minimalism and Swiss style. A grid, one sans-serif family, whitespace, high contrast. Hierarchy comes from size, weight and spacing.

## Global Rules

### Color Palette

Both themes follow `prefers-color-scheme`. Every text pair below is at least 4.5:1, and every control edge and focus ring at least 3:1 against what it sits on (checked 2026-11-04).

| Role | CSS variable | Light | Dark | Use |
|---|---|---|---|---|
| Page | `--color-bg` | `#F8FAF9` | `#0E1413` | Page background: a neutral off-white, not cream |
| Surface | `--color-surface` | `#FFFFFF` | `#151C1B` | Inputs, dialogs, the printed recipe page |
| Surface 2 | `--color-surface-2` | `#EDF2F1` | `#1D2625` | Hover rows, table header band |
| Selected | `--color-selected` | `#D7EFEC` | `#163A36` | The selected day or row |
| Text | `--color-text` | `#142120` | `#E6EDEC` | Body, headings, numbers (15.8:1 / 15.7:1 on the page) |
| Muted text | `--color-text-muted` | `#4B5A58` | `#A2B0AE` | Units, hints, meta (6.9:1 / 8.3:1) |
| Rule | `--color-border` | `#D5DEDC` | `#2B3634` | Lines between rows and groups (decorative) |
| Control edge | `--color-border-strong` | `#73817F` | `#6E7E7B` | Input and secondary button edges (3.6:1 or more) |
| Primary | `--color-primary` | `#0F766E` | `#5CD6C4` | Primary buttons, links, focus rings, progress fill (5.2:1 / 10.5:1) |
| Primary hover | `--color-primary-hover` | `#115E59` | `#8BE5D7` | Hover and pressed state of the above |
| On primary | `--color-on-primary` | `#FFFFFF` | `#06302C` | Text on a primary button (5.5:1 / 8.1:1) |
| Meets | `--color-ok` | `#166534` | `#86EFAC` | "Meets the target" words |
| Misses | `--color-fail` | `#B42318` | `#FDA29B` | "Misses the target" words, field errors |
| Warning | `--color-warn` | `#8A5300` | `#F7C46C` | No-waste warnings, "storage is not saved" |
| Can't tell | `--color-unsettled` | `#4B5A58` | `#A2B0AE` | Unsettled checks: muted, with the words saying why |

**Colour notes:** teal is the only accent. The status colours appear on words only (never as fills, stripes or dots), and each status always has its word: "Meets", "Misses", "Can't tell", "Warning".

### Typography

- **Family:** Plus Jakarta Sans (variable, weights 400 to 700) for everything, self-hosted from the `@fontsource-variable/plus-jakarta-sans` package so no request leaves for a font CDN. Fallback: `system-ui, sans-serif`.
- **Sizes:** page title 28px/600, section heading 22px/600, sub-heading 18px/600, body 16px/1.6, tables and meta 14px/1.5, smallest 13px (units beside a number). Nothing under 13px.
- **Numbers:** every column of numbers uses `font-variant-numeric: tabular-nums` and is right-aligned; the unit sits in muted text after the number ("412 kcal").
- **No** italic headlines, no display or serif face, no all-caps labels above headings, no letter-spaced body text.

### Spacing Variables

*Density 5/10, standard. A 4px base.*

| Token | Value | Usage |
|-------|-------|-------|
| `--space-xs` | `4px` / `0.25rem` | Tight gaps |
| `--space-sm` | `8px` / `0.5rem` | Icon gaps, inline spacing |
| `--space-md` | `16px` / `1rem` | Standard padding, the phone gutter |
| `--space-lg` | `24px` / `1.5rem` | Between groups |
| `--space-xl` | `32px` / `2rem` | Between sections |
| `--space-2xl` | `48px` / `3rem` | Page top and bottom |
| `--space-3xl` | `64px` / `4rem` | Rarely; wide screens only |

### Shape and depth

| Token | Value | Usage |
|-------|-------|-------|
| `--radius-sm` | `4px` | Inputs, buttons |
| `--radius-md` | `8px` | Dialogs, the selected row background |
| `--shadow-overlay` | `0 8px 24px rgba(0, 0, 0, 0.18)` | Dialogs and menus only, because they sit above the page |

Nothing on the page itself has a shadow. Groups are separated by a rule (`1px solid var(--color-border)`) and whitespace, not by boxes.

### Layout

- **Phones (below 768px):** one column, 16px side gutters, no horizontal scroll at 375px. Navigation is a row of text links under the app name (Plan, Week, Log, Summary).
- **Wider screens:** content up to 72rem wide; reading text (recipe steps, notes) at most 40rem wide. The week shows days as rows and slots as columns from 1024px; below that, one day at a time with previous and next.
- **Touch targets:** 44px tall at least, through padding.

---

## Component Specs

### Buttons

```css
/* Primary: one per view, for the main action (Write the week, Save the meal). */
.button-primary {
  background: var(--color-primary);
  color: var(--color-on-primary);
  border: 1px solid var(--color-primary);
  min-height: 44px;
  padding: 0 var(--space-md);
  border-radius: var(--radius-sm);
  font-weight: 600;
  transition: background-color 120ms ease, border-color 120ms ease;
  cursor: pointer;
}
.button-primary:hover { background: var(--color-primary-hover); border-color: var(--color-primary-hover); }

/* Secondary: everything else that is a button. */
.button-secondary {
  background: transparent;
  color: var(--color-text);
  border: 1px solid var(--color-border-strong);
  /* same size, radius, weight and transition as the primary */
}
.button-secondary:hover { background: var(--color-surface-2); }

/* Text button: actions inside a row (Change food, Remove). An underlined word, no border. */
.button-text { background: none; border: 0; color: var(--color-primary); text-decoration: underline; }
```

A button that starts a model call says what it does ("Write the week") and is disabled with a reason while a call runs. No hover lift, no `transition: all`.

### Inputs

```css
.input {
  min-height: 44px;
  padding: 0 var(--space-sm);
  background: var(--color-surface);
  color: var(--color-text);
  border: 1px solid var(--color-border-strong);
  border-radius: var(--radius-sm);
  font-size: 16px; /* 16px stops phones zooming into the field */
}
.input:focus-visible { outline: 2px solid var(--color-primary); outline-offset: 2px; }
.input[aria-invalid="true"] { border-color: var(--color-fail); }
```

Every field has a visible label above it and, when the server or the import check refuses it, the message under it in `--color-fail`, tied with `aria-describedby`. A form with errors also lists them at the top, each linking to its field.

### Tables of numbers

Nutrients per recipe, per day and per week. A header row on `--color-surface-2`, rows split by rules, numbers right-aligned in tabular figures, the unit in muted text. A total row is weight 600 with a rule above it. A lower-bound total (a food lacks the value) shows "at least 412" and says why in a footnote, never a bare number.

### Status

A word, then the reason: "Misses: protein 31 g of at least 40 g". The word takes the status colour; the reason stays in body text. An unsettled check reads "Can't tell: no fibre value for dal" in muted text.

### Live progress (writing a week)

A list of slots, each with its current step in words ("Writing", "Checking", "Repairing, try 2 of 4", "Done", "Could not write") inside an `aria-live="polite"` region, and one determinate progress bar (`<progress>`) for slots finished out of slots asked. No spinner, no pulsing, no skeleton shimmer.

### Notices

Plain text with a heading, a 1px rule above and below. Used for "Your data lives in this browser only. Export it to keep a copy." and for a failed save. No coloured side stripe, no icon badge.

### Dialogs

The native `<dialog>` element, `--color-surface`, `--radius-md`, `--shadow-overlay`, a plain dim backdrop (`rgba(0, 0, 0, 0.5)`, no blur). Escape closes it and focus returns to the control that opened it.

### Print (one recipe per A4 page)

`@page { size: A4; margin: 15mm; }`, black text on white whatever the theme, navigation and buttons hidden, each recipe starting a new page (`break-before: page`), grams in a table and short numbered steps.

---

## Motion

Only colour and background changes, at 120ms, to confirm a hover or press. No scroll reveals, no entrance animations, no hover zooms or lifts, no pulsing or blinking. `prefers-reduced-motion: reduce` sets every duration to 0.

## Icons

None are needed for the base. When a screen needs one, use Lucide's SVG icons beside a text label, with `aria-hidden="true"` on the icon. An icon alone is never the only label.

## Accessibility

Landmarks (`header`, `nav`, `main`), a skip link, full keyboard use with a visible 2px primary focus ring, labels on every control, `aria-live` for progress and for save results, WCAG 2 AA contrast in both themes, and usable at 375px wide and at 200% zoom.

## Avoid (from the portfolio's design system; these read as machine-made)

- Cream or beige backgrounds with a terracotta, rust or acid-green accent; purple-to-blue gradients; gradient text; glow halos.
- Display serifs, italic serif headlines, oversized hero headlines, all-caps eyebrow labels above headings, tracked-out body text.
- Cards in identical grids, cards inside cards, pills everywhere, thick coloured stripes on card edges, hairline border plus soft shadow.
- Pulsing status dots, blinking cursors, bouncy easing, content hidden until an entrance animation runs.
- Emoji anywhere, marketing phrases, forced contrasts ("Not X. Y."), em-dash-heavy copy.
- Any credit line for the tools used to build the app.

## Pre-Delivery Checklist

- [ ] Colours, sizes and spacing from `tokens.css`; nothing on the Avoid list
- [ ] Text contrast at least 4.5:1 and control edges at least 3:1 in both themes
- [ ] Every status has its word; colour is never the only signal
- [ ] Keyboard path works, focus is visible, Escape closes dialogs
- [ ] No horizontal scroll at 375, 768, 1024 and 1440px
- [ ] `prefers-reduced-motion` respected
- [ ] Every number shown comes from an API response, never from the page's own arithmetic beyond adding what the API returned

---

## Changes from the starting point, and why

A first starting point, pink with gold and a script font made for wedding sites, did not fit a food tool and was dropped. The second, for a nutrition-tracking productivity tool, was kept where it fits a daily tool and changed by hand where it conflicts with the portfolio's Avoid list or with this product.

| Started as | Now | Why |
|---|---|---|
| Accent/CTA `#EA580C` orange with black text, used for the primary button | Removed; the primary button is teal | A burnt orange call to action is the terracotta-accent look the Avoid list names, and a second accent colour competes with the first. One accent keeps "this is an action" unambiguous |
| Primary `#0D9488` with black text on it | `#0F766E` with white text (`#5CD6C4` with dark text in dark mode) | `#0D9488` is 3.7:1 on white, under AA for links and button text. `#0F766E` is 5.5:1 |
| Secondary `#14B8A6` | Dropped | Two teals in one interface read as decoration; nothing needed a second accent |
| Background `#F0FDFA` (a mint tint) and foreground `#134E4A` | `#F8FAF9` and `#142120` | A tinted page with tinted text is a theme, not a calm tool; a near-neutral page keeps the teal meaningful |
| Border `#99F6E4` (bright teal) | `#D5DEDC` rules and `#73817F` control edges | Bright teal borders everywhere are decoration; control edges also need 3:1, which `#99F6E4` does not reach on white |
| Light theme only | A full dark theme | The brief asks for high contrast in light and dark |
| Shadow scale `sm` to `xl`, with cards on `--shadow-md` | One overlay shadow for dialogs and menus | "Hairline border plus soft shadow" and cards everywhere are on the Avoid list; rules and whitespace group content instead |
| Card component with 12px radius, shadow, hover lift (`translateY(-2px)`) and `cursor: pointer` | No card component | Cards in grids and hover lifts are on the Avoid list. The week is a table, a recipe is a page |
| Buttons with `transition: all 200ms` and `translateY(-1px)` on hover | Colour-only transitions at 120ms, no movement | Moving controls are the bouncy, decorative motion the Avoid list names; `all` also animates layout |
| Modal backdrop `blur(4px)` | Plain dim backdrop | Blur is decoration and costs performance on low-end phones |
| Page pattern "Product Demo + Features" (hero, product video, features, CTA) | No landing pattern; the app opens on the plan or the week | It is a tool people return to daily, not a marketing page, and an oversized hero is on the Avoid list |
| Motion "Scroll Reveal" with GSAP (fade and rise on scroll) | No entrance motion and no GSAP | "Content hidden until an entrance animation runs" is on the Avoid list |
| Typography loaded from Google Fonts by `@import` | The same family, self-hosted from npm | The app's promise is that the user's data stays in their browser; a font CDN request on every visit tells a third party who opened it, and the app should work offline once loaded |
| Weights 300 to 700 | 400 to 700, nothing under 13px | 300 on small numbers fails readability in a kitchen |
| Emoji markers in the starting point's anti-pattern and motion lists | Plain text | Emoji anywhere is on the Avoid list |
| Spacing scale, Minimalism and Swiss style, Plus Jakarta Sans as the one family | Kept | They fit a plain, readable tool and nothing on the Avoid list rules them out |
