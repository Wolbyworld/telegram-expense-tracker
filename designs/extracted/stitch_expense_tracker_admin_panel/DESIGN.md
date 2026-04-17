# Design System Strategy: The Fluid Ledger

This design system is built for a premium personal finance experience that transcends the typical "grid-and-border" fintech aesthetic. It is designed to feel less like a database and more like a high-end editorial publication—clean, authoritative, yet approachable.

### 1. Overview & Creative North Star
**Creative North Star: "The Digital Curator"**
The design system rejects the "busy" nature of traditional finance apps. Instead of overwhelming the user with dense tables, it treats financial data as curated content. 
- **Intentional Asymmetry:** We break the rigid dashboard grid by using varying card widths and staggered vertical alignments to guide the eye toward "Primary Insights" first.
- **Breathing Room:** We utilize "Generous Whitespace" not just as padding, but as a functional element to lower cognitive load and convey a sense of calm and control over one's finances.
- **Editorial Scale:** By pairing the organic, geometric curves of **Manrope** for numbers and headings with the functional clarity of **Inter** for data, we create a signature typographic rhythm that feels custom-built.

---

### 2. Color & Tonal Depth
Our palette is anchored in a sophisticated Teal/Emerald, but its application is governed by three "Editorial Rules" to ensure a high-end feel.

**The "No-Line" Rule**
1px solid borders are strictly prohibited for sectioning. Structural boundaries must be defined solely through background color shifts.
- **Base Layer:** Use `surface` (#f5faf8) for the main application background.
- **Sectioning:** Use `surface_container_low` (#f0f5f2) to define larger layout regions (like a sidebar or a secondary feed).
- **The Nested Depth:** A card using `surface_container_lowest` (#ffffff) sitting on a `surface_container` (#eaefed) background provides all the definition a user needs without "trapping" content in boxes.

**Signature Textures & Glassmorphism**
To avoid a flat "template" look:
- **Hero CTAs:** Use a subtle linear gradient for primary buttons, transitioning from `primary` (#00685f) to `primary_container` (#008378) at a 135° angle.
- **Floating Overlays:** Modals and dropdowns should utilize a "Glass" effect: `surface_container_lowest` at 85% opacity with a `20px` backdrop-blur. This allows the emerald tones of the dashboard to bleed through softly.

---

### 3. Typography
We employ a dual-typeface system to balance professional authority with friendly accessibility.

| Role | Typeface | Token | Intent |
| :--- | :--- | :--- | :--- |
| **Display** | Manrope | `display-lg` | Large-scale wealth totals or hero balances. |
| **Headline** | Manrope | `headline-sm` | Section titles (e.g., "Monthly Spending"). |
| **Title** | Inter | `title-md` | Card titles and primary navigation items. |
| **Body** | Inter | `body-md` | Standard data, descriptions, and list items. |
| **Label** | Inter | `label-sm` | Captions, dates, and micro-copy. |

*Note: Use `on_surface_variant` (#3d4947) for body text to reduce harsh contrast and create a "premium ink" feel.*

---

### 4. Elevation & Depth
Depth is achieved through "Tonal Layering" rather than structural scaffolding.

- **The Layering Principle:** Stack surfaces to create hierarchy.
    *   *Level 0:* `surface` (Global background)
    *   *Level 1:* `surface_container` (Content groupings)
    *   *Level 2:* `surface_container_lowest` (Interactive cards/Active states)
- **Ambient Shadows:** Only use shadows for elements that "float" (Modals, Hovered Cards). 
    *   **Value:** `0px 12px 32px rgba(23, 29, 28, 0.06)`. The shadow color is a 6% tint of `on_surface` to mimic natural light.
- **The "Ghost Border" Fallback:** If a divider is functionally required for accessibility, use the `outline_variant` (#bcc9c6) at **15% opacity**. Never use 100% opaque lines.

---

### 5. Components

**Buttons (Signature Style)**
- **Primary:** `primary` background, `on_primary` text. Use `xl` (1.5rem) roundedness for a friendly, modern feel.
- **Secondary:** `secondary_container` background with `on_secondary_container` text. No border.
- **Tertiary:** No background; `primary` text. Used for "Cancel" or "Go Back" actions.

**Cards & Data Lists**
- **The "No-Divider" Rule:** In transaction lists, do not use lines between items. Instead, use 12px of vertical spacing and a subtle `surface_container_low` background on hover to define the row.
- **Expense Chips:** Use `tertiary_container` for "High Spending" alerts and `secondary_container` for "On Track" status.

**Input Fields**
- Fields should use `surface_container_highest` with a `none` border. On focus, transition to a 2px `primary` bottom-border only (Editorial Input style) rather than a full box stroke.

**Special Fintech Component: The Wealth Curve**
A custom area chart component using a gradient fill: `primary` at 20% opacity at the peak, fading to 0% at the base. The line itself should be a 3px thick `primary` stroke.

---

### 6. Do’s and Don’ts

**Do:**
- **Do** use `DEFAULT` (0.5rem) or `md` (0.75rem) corner radii for most components to maintain the "Friendly" aesthetic.
- **Do** utilize "Staggered Loading" animations—elements should slide up slightly (4px) as they fade in to reinforce the sense of physical layers.
- **Do** prioritize `tertiary` (#924628) for alerts; its warm, earthy tone is less "alarming" than pure red, fitting the "Professional & Friendly" vibe.

**Don’t:**
- **Don't** use pure black (#000000) for text. Use `on_surface` (#171d1c) for high-contrast headlines and `on_surface_variant` (#3d4947) for secondary text.
- **Don't** use standard Material shadows. They are too "heavy." Stick to our Ambient Shadow values.
- **Don't** center-align long lists of financial data. Always left-align text and right-align numerical values to create a "spine" down the center of the list.

**The Golden Rule of this system:** If the interface feels "boxed in," remove a border and add 16px of whitespace. Let the typography and color shifts do the work.