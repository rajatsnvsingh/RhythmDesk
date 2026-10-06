# UX overhaul: implementation and verification

[User guide](user-guide.md) · [Architecture](architecture.md) · [Technical specification](technical-spec.md)

The eight work packages from the usability review are implemented together. The guiding rule is **dense information, deliberate actions**: reduce repeated forms and navigation, not the evidence needed to trust a release.

## What changed

| Work package | Delivered behavior |
| --- | --- |
| Reliable edits and feedback | Explicit draft model; helper inputs are not metadata changes; errors inside the active dialog; guarded dirty X/Escape/backdrop/navigation; refresh preserves drafts |
| Honest readiness | Current revision, state, policy, destination and publisher blockers; prepared-for-review is separate from publication permission |
| Persistent actions / mobile | One scrolling inspector body, stable in-modal player, reachable Save/Review footer, compact rows, labelled sorting and page selection |
| Home and Review | Home / Incoming / Review / Library; Import and Scan always accessible; Decisions includes attention and ready; processing/history distinct; counts computed before paging |
| Unified editing | One metadata/artwork/label editor and Save; searchable chips with expert text fields; explicit selected-track Add/Replace; unknown Allow/Map/Remove and Create |
| Import and recovery | Server/device chooser, breadcrumbs, selected-path tray, persistent transfer status, manual Scan after completion, truthful failure explanations and edition links; manual grouping accepts untagged sources |
| Statistics and Settings | Operational vs retained job history vs current library; timestamps; genre/tag coverage and artwork-presence caveats; staging categories; live controls before deployment details; isolated purge danger zone |
| Regression and validation | Backend/editor regressions, CI editor tests and syntax checks, synthetic browser walkthroughs, narrow reflow and large-release fixtures; physical-device checklist below |

On desktop, Home places decisions beside counts and library context. On phones, decision actions come first. Track titles, artists, positions, length/encoding, artwork presence, genres and category tags remain visible without opening each track. Edit tools appear on demand; original evidence and raw logs are secondary disclosures.

## Unchanged safety boundaries

- No scan starts merely because files appeared. No high-confidence match publishes automatically.
- All submitted tracks must validate. No successful subset is silently approved.
- Edits affect disposable working copies, retain originals and generate a new review revision.
- The server and publisher remain authoritative: presentation readiness cannot bypass byte/revision checks, allowed labels, explicit approval or no-replacement rules.
- No new bulk-publish, library-edit or force-overwrite control exists.
- Purge still requires a checkbox, coordinates with worker activity and excludes library/state archives. No purge was performed on Nexus during verification.
- Deployment changes source only; administrator-pinned mounts, identities, Beets thresholds, credentials and container isolation are unchanged.

## Verification performed

Backend tests cover artwork replacement on every output track with unchanged originals, stale revisions, unknown labels, destination collisions, read-only coverage/storage, manual grouping without invented metadata and more than 1,000 queue entries. Existing authentication, import, range playback, mode, publication and archive tests remain in the suite. Pure editor tests cover dirty scope, exact revision payloads, source identities, mapping/removal and selected-only Add/Replace.

Browser walkthroughs used only synthetic music. They checked label creation alongside dirty metadata; chip addition/removal and visible mapping errors; Save creating a new revision; dirty close/Escape; View all Decisions; conditional manual grouping; copy-only source import with Scan offered but not started; sorting/page selection; browser Back; and in-modal playback with advancing time and no decoder error. Real library publication and purge were not used as UI tests.

Checked phone widths 320, 390 and 430, tablet 768, and desktop layouts. Incoming, Review, Settings and Library reflow without page-level horizontal overflow in the checked states. Real 20- and 50-track synthetic releases include long titles and regional characters. Primary review actions stay at the bottom of the viewport while tracks scroll.

## Physical-phone acceptance check

Browser emulation does not certify a physical phone. Before treating mobile behavior as fully validated, check on the devices you actually use:

1. Import access, navigation and page selection by touch.
2. Edit a low-down track with the keyboard open; the focused input and Save must remain reachable.
3. Rotate the phone, scroll a 20/50-track release, and check safe-area spacing.
4. Close/back out of a dirty draft; confirm Keep editing preserves it.
5. Play/pause/seek a real track, close the inspector, and confirm playback stops.
6. Review a real prepared release; verify its actual edition and metadata before explicit publication.

## Known limits

Draft protection is in-session, not crash recovery: drafts are not stored in local storage. A server-side revision conflict preserves the local draft but requires discarding/reloading before another Save. Source import IDs can restore transfer status after refresh; an unfinished device upload needs its retained browser files or reselection. Existing cached library statistics may need one read-only **Rescan library stats** to populate the new label coverage fields. Artwork coverage counts presence, not decoded image validity or source fidelity. Matching recovery does not invent per-recording success counts when the matching log lacks structured evidence.
