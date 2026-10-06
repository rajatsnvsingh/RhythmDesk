# Rhythm Desk user guide

[Documentation index](README.md) · [Deployment](deployment.md) · [Technical specification](technical-spec.md)

Your library is the destination, not the workspace. Rhythm Desk copies music into staging, prepares a reviewable release and waits for your decision. **Curated does not mean published.**

## 1. Get your bearings

![Overview](assets/screenshots/overview.jpg)

The overview shows incoming tracks, active curation, releases waiting for approval and approved releases. Library statistics include albums, tracks, artists, storage, genres and category tags. Activity records imports, matching, corrections and publication.

Processing progress appears here and inside Inspect. Preparing/manual tagging shows track counts. Matching and release resolution can wait on external services; their indicators are deliberately indeterminate. An elapsed timer is not an estimated time remaining.

## 2. Bring music into Incoming

You do **not** need to organize everything into album folders first.

### Copy from the server's music drawer

![Server-side music drawer](assets/screenshots/music-drawer.jpg)

1. Open **Incoming → Browse Nexus music**.
2. Open a folder by clicking its name. Use **Parent**, **Root** or the current-folder filter to navigate.
3. Check files/folders you want. Selections persist as you browse.
4. Click **Copy selected into Incoming** and wait for Complete.

The configured source is read-only. A selected folder includes subfolders; copies preserve relative paths inside a new `Import-<id>` batch. The picker excludes operational staging/state/library directories. Importing does not move originals, run a scan or approve anything.

### Upload from your computer

Use **Choose files**, **Choose folder**, or drag files/folders onto **Drop music into staging**. The browser copies the files, leaving your computer's originals alone. A completed upload appears as `Upload-<id>` in Incoming. Folder selection support depends on your browser.

### Copy over SMB

You can also copy files to the host's `<STAGING_PATH>/incoming` through an administrator-configured SMB share. The app does not configure SMB. Finish your transfer before scanning; avoid editing files while the app is inspecting/copying them.

Limits: browser uploads allow 2 GiB per file; upload/import batches allow 50 GiB and 10,000 files. Use smaller batches or SMB for larger collections.

## 3. Scan when you are ready

![Incoming inventory](assets/screenshots/incoming.jpg)

The app detects changes and enables **Scan & prepare**, also available on Overview. Detection does not start a scan. Click it after your copy/upload finishes.

Scanning reads audio metadata and quality details, fingerprints file bytes for exact duplicate detection, excludes tracks under 10 seconds and indexes non-audio files as ignored. Originals remain in Incoming. Automatic grouping, when enabled in Settings, groups eligible tracks using their album tags.

Unidentified tracks stay Unresolved. Select related tracks, choose **Group selected tracks**, supply an album artist/title and choose a matching mode. You can return an idle unpublished job's tracks to intake and regroup them if the grouping is wrong.

## 4. Choose the right matching mode

| Mode | Choose it when | What must succeed |
| --- | --- | --- |
| Auto | You want the normal default | One file selects Single; multiple files select Complete album |
| Complete album | You believe you have a whole release | Strict Beets matching and import of every submitted eligible track |
| Single track | You have exactly one song | Recording match plus unambiguous/selected release membership |
| Partial album | You intentionally have only part of a release | Every submitted recording matches and belongs to the intended release |
| Manual metadata | Catalogue matching cannot represent your release | You supply valid metadata for every submitted track; no completeness certification |

“Found 5 candidates” means five possible catalogue matches, not five imported songs. If submitted tracks are skipped, the release remains Needs review: the app does not silently publish a successful subset.

Website addresses such as `songs.pk`, `MP3Khan.com` and `djpunjab.com` are cleaned from working-copy title, album and artist fields before automatic matching. Original metadata is retained. The cleaner does not relax confidence thresholds or guarantee a match.

## 5. Resolve Needs review

Open **Inspect** and read the reason and matching log. Confirm the source tracks really belong together. You can retry using a different matching mode or an exact MusicBrainz release UUID when you know the edition.

**Retry & replace artwork** asks for a downloaded cover even if existing covers are present. Automatic matching must still succeed; this is not a force-import button. A failed network lookup, missing recording match or ambiguous release edition needs resolution, not a weaker approval gate.

## 6. Curate manually

![Manual metadata and artwork](assets/screenshots/manual-curation.jpg)

Choose Manual when grouping, or open an idle unpublished release and click **Manual metadata & artwork**.

1. Set album artist, album title and a four-digit release year. For a soundtrack, use the album artist you want the final folder to represent; individual singers belong in track artist fields.
2. Check every title, track artist and disc/track position. Duplicate positions are rejected.
3. Set genres and category tags, separated by semicolons.
4. To replace artwork, choose an image file or click the paste box and paste a copied **image**, not a URL. On desktop, use Ctrl+V/Cmd+V. File selection is the practical fallback on phones.
5. Click **Prepare manual Curated copy** and wait for curation to finish.

Artwork uploads are limited to 10 MiB and 20 megapixels. Images are normalized to JPEG with a maximum 1600-pixel side and embedded into every output track. Without a replacement, valid existing covers are retained. Manual curation scrubs disposable-copy metadata, then writes your values; it does not download artwork or query MusicBrainz.

Manual releases are clearly marked user-supplied. You still need to inspect and explicitly approve them. Unknown genres/category tags still block publication.

## 7. Inspect the Curated output

![Release inspector](assets/screenshots/inspector.jpg)

Check the cover, edition, proposed destination, album metadata, track titles/artists and numbering. Click a track's play button to preview it inside the inspector. Expand Source tracks, embedded artwork or the log when useful; artwork is collapsed by default.

Save corrections before requesting approval. A saved edit changes the reviewed audio revision. Illegal filename characters such as `/` become safe substitutes in filenames; editable track-title metadata can retain the actual title.

### Genres and category tags

Expand **Genres & category tags**. Unknown values are highlighted and block approval. You can remove/correct them, explicitly allow a value, or create an allowed label from the inspector. Creating a label does not apply it automatically: enter it in track fields and save corrections.

Allow-list matching ignores case. Category tags are user-facing labels stored in the grouping field, not a list of every technical audio tag. Empty allow lists permit no non-empty values. You can also manage global lists in Settings.

## 8. Approve and publish

Choose **Review approval**, verify the destination and track count, acknowledge the confirmation and approve the exact reviewed revision. Only this action asks the publisher to copy the release into Rhythm Attic.

If that destination already exists, publication is blocked. There is no overwrite/replace option. Changed audio or a stale revision also blocks publication. A successful publication records an audit receipt; repeating that successful approval is idempotent.

Completed work is archived under state/processed. Curation is copy-based; don't assume Incoming has been emptied after approval.

## 9. Use your phone

<img src="assets/screenshots/mobile-review.jpg" alt="Phone-sized release inspector" width="340">

Connect to the same trusted network and open the server's LAN URL. `localhost` on your phone means your phone, not the server. The supplied Compose configuration is localhost-only until an administrator changes it. The mobile UI uses scrolling navigation, labelled data cards, larger touch controls and responsive dialogs.

Authentication sessions normally survive container restarts for their 12-hour lifetime. Changing hostname/origin, expiring the session or rotating the server token can require sign-in again.

## 10. Settings and cleanup

Settings shows effective Docker paths, runtime intake controls, fixed protections, allow lists, staging storage and the read-only source mapping. A saved directory plan is advisory: it does not remount Docker or change the current publishing destination. Source subfolder selection takes effect inside the configured read-only mount.

**Purge everything in staging is permanent.** It removes incoming originals, unpublished curated copies, needs-review work and abandoned upload/import batches. Library contents and processed state archives are retained. Confirm the checkbox only when you intend to discard all staging content. Stop SMB transfers and avoid editing/approving while purging. The app coordinates with the worker automatically; routine use does not require manually stopping it.

## Troubleshooting

| Symptom | Next check |
| --- | --- |
| Drawer not mounted/readable | Settings source mapping, Docker mount and UI account read/traverse permissions |
| Scan disabled after a copy | Wait for copy Complete, refresh, and check whether files are already indexed unchanged |
| Matching has candidates but skips | Read the log; correct metadata/edition, retry the proper mode, or use manual curation |
| Some files match, others don't | All submitted tracks must succeed; regroup or manually curate, rather than publishing a subset |
| Approval disabled | Unapproved labels, unsaved/manual edits, an existing destination, demo mode or unavailable publisher |
| Worker not reporting | Check `docker compose ps` and worker logs; shared SQLite/WAL/SHM permissions matter |
| Artwork looks broken | Inspect source artwork validity; choose a replacement in manual mode or retry with fetched art |
| Playback fails everywhere, including other sites | Check the browser/device audio system before attributing it to the app |

For administrator commands, backups, deployment access and network setup, see [Deployment & operations](deployment.md).
