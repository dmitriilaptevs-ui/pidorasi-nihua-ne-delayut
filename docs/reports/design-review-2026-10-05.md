# Design review: product pages (auth, account, landing)

**Model:** openai-codex/gpt-6.1-sol (subscription) — review requested per the goal contract.
**Date:** 2026-10-05. **Scope:** static review of `apps/web/src/components/{landing,auth-panel,account-panel}.tsx`, `panel.css`, `layout.tsx`, `globals.css`. No edits, servers or browser runs by the reviewer.

## Reviewer summary

The warm paper / ink / lime canvas language with hard borders and offset shadows is coherent and was kept. The reviewer's highest-impact findings and their disposition:

| # | Finding | Disposition |
| --- | --- | --- |
| 1 | Mobile 390px: `.rb-card--narrow` overflowed (padding outside 100%), navigation crowded, long emails did not wrap | Applied: global `box-sizing` inside `.rb-page`, stacked nav on mobile, `overflow-wrap: anywhere` for nav text |
| 2 | Heading hierarchy: landing hero and auth shared one size; feature titles used dashboard stat-label styling | Applied: desktop-only hero size, `.rb-feature__title` in Unbounded, account page H1 «Личный кабинет» |
| 3 | Loading, empty and error states were conflated ("обновляется", spinner text after failure) | Applied: explicit loading/error/retry on the landing catalog and in the account; empty-filter message |
| 4 | Keyboard focus and tap targets: only inputs had custom focus; small buttons under 44px; scrollable tables not focusable | Applied: ink focus outline for buttons/links/scroll regions, `min-height: 44px` for small buttons, `tabIndex=0` + `role=region` on table wrappers |
| 5 | Outcomes not announced; busy buttons kept their labels; verification without a token had no explanation | Applied: `role="alert"` / `role="status"`, action-specific pending label, explicit invalid-link message, forgot-password tooltip |
| 6 | One-time key workflow: copy success reported even on failure; revoke had no confirmation or distinct accessible name | Applied: success only after the clipboard write resolves (with manual-copy fallback), confirm dialog naming the key, `aria-label` per revoke button |
| 7 | Catalog counts and units: "first 40" shown even when fewer; «₽/Мток» and "Tools" jargon | Applied: real shown/found counts, «₽ за 1 млн токенов», «Инструменты» |
| 8 | Russian copy foregrounded internal mechanics | Applied to features/balance text: benefits first, sandbox top-up explained in plain words |

Canvas inconsistencies also fixed: selection colour moved from blue to lime/ink, and the navigation underline uses the same 3px border language as the rest of the UI.

## Kept as-is (reviewer: do not change)

- Warm paper, ink, lime, square corners and offset shadows.
- Explicit form labels, password autocomplete and `lang="ru"`.
- Local table scrolling and suppression of hover motion on touch devices.
