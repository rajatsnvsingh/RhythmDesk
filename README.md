# Rhythm Desk

A self-hosted music curation desk powered by Beets, MusicBrainz, ffprobe, and a
compact Web UI. Import messy collections, inspect cleaned releases, then explicitly
approve publication. Acquisition tools such as slskd remain separate.

## Safety model

- Nothing publishes automatically, regardless of match confidence.
- Originals are retained. Curation works on copies.
- Unknown genres/category tags block publication until corrected or allowed.
- Ambiguous matches remain Needs review; exact MusicBrainz release IDs can resolve editions.
- Existing library destinations are never replaced. Changed audio invalidates approval.
- Final output contains labelled audio with embedded artwork. Non-audio files and tracks
  shorter than 10 seconds are excluded from output, not deleted from originals.
- Worker, web, and publisher use separate non-root identities. Only publisher has a
  writable library mount; the worker cannot reach its publishing socket.

Output: Artist/Album (Year)/NN - Track.ext. Filename characters are sanitized while
metadata retains actual titles. Album, single-track, and partial-release matching
are supported; incomplete albums require appropriate matching mode and explicit review.

## Docker setup

Prerequisites: Docker Compose on Linux, Python 3 for bootstrap, and ACL tools (setfacl).

1. Copy .env.example to .env. Set a strong random CURATOR_UI_TOKEN, staging/state/library
   paths, and SOURCE_PATH for the read-only music drawer.
2. Create the library directory if starting fresh. Run:
   sudo python3 app/bootstrap.py --staging <path> --state <path> --library <path>
   to provision restricted service identities and scoped ACLs. Inspect the script first.
3. Give the UI identity read/traverse permissions on selected drawer directories, not write.
   Docker additionally mounts this source read-only.
4. Run docker compose up --build -d and verify all three services remain running.
5. The example UI binds to localhost. Use an SSH tunnel or deliberately configure a LAN
   binding and CURATOR_ALLOWED_HOSTS. Never expose plain HTTP to the Internet.

Default worker/UI/publisher UIDs are 10001/10002/10003 with shared group 10000.
Check for ID conflicts before bootstrap. Shared SQLite database, WAL and SHM files
must remain group-writable. Back up state before maintenance.

## Ingestion and the music drawer

The drawer is a read-only host music folder configured by SOURCE_PATH and mounted
at /mnt/music-source in the web container. Settings shows this mapping and selects
a relative subfolder without changing Docker mounts. Host mount changes require
administrator configuration.

Incoming is the writable ingestion folder at <STAGING_PATH>/incoming.
Use Incoming → Browse Nexus music to select files or folders. Selections are copied
server-side with relative paths preserved. Source files are never moved or edited.
Staging, state, and published-library folders are excluded to avoid recursive imports.
A whole copied batch becomes visible as incoming/Import-<id>/ only after completion.
The UI shows progress; interrupted copies remain outside Incoming for inspection.

Alternatively, drag files/folders from your computer or use the file/folder pickers.
Uploads stream to temporary staging, then expose incoming/Upload-<id>/ only after
all files succeed. Files on your computer remain untouched. Re-select failed batches
to retry; abandoned temporary files are included in staging statistics and purge.

Click Scan when ready. New files are detected, but scanning remains user-controlled.
Browser limit: 2 GiB per file. Upload batches and drawer imports: 50 GiB/10,000 files.
SMB is suitable for larger transfers.

## Review and publish

1. Scan Incoming, check grouping, or manually group loose tracks.
2. Resolve Needs review items: select a matching mode or exact release ID.
3. Inspect Curated output: edition, titles, numbering, artist/year, audio and artwork.
4. Correct genres/category tags or explicitly allow/create them in the label section.
5. Review and approve the exact audio revision to publish it.

Usable embedded covers are kept if every track has one. Fetched replacements are
embedded in applicable output tracks. Retry and replace artwork is available.
Mobile screens use labelled cards, touch-sized controls, responsive dialogs, and
an inspector player. Phones need the LAN address, not localhost.

Settings includes staging file count/size and permanent purge with checkbox confirmation.
Purge coordinates with active staging work. It deletes all staging originals, unpublished
and abandoned batches. Published music and state archives are untouched.
Do not purge unless you intend to discard everything in staging.

## Authentication and configuration

### Manual curation and processing progress

Choose Manual metadata when grouping, or inspect an unpublished release and select
Manual metadata & artwork. Enter album artist/title/year and each track's title,
artist, disc/track position, genres and category tags. Paste an image into the
artwork box or choose an image file (10 MiB / 20 megapixel limit). Replacement
covers are normalized and embedded in every output track; without a replacement,
valid original covers are retained. Manual curation bypasses catalogue matching,
scrubs working-copy tags, and prepares a new Curated revision. It does not certify
album completeness or authorize publication. Unknown labels still block approval.

Overview and the inspector show current processing stage, elapsed time and track
counts where available. MusicBrainz/Beets matching uses an indeterminate progress
bar rather than a guessed percentage. Active jobs refresh every two seconds.

Before automatic matching, distributor website watermarks (including songs.pk,
MP3Khan.com and djpunjab.com) are removed from title/album/artist fields in working
copies only. Each change is logged. Originals and match thresholds are unchanged.

Token authentication is enabled by default. The raw token is not stored in browser
storage. Sign-in creates an HttpOnly SameSite cookie valid for 12 hours; hashed
session identifiers persist in state across container restarts. Token rotation
revokes sessions. API mutations verify session, CSRF token and origin.

CURATOR_AUTH_REQUIRED=0 is supported but not recommended. Use a trusted LAN or HTTPS.
Docker directory plans in Settings are advisory, not automatic remount operations.
Library paths cannot be changed through publishing actions.

## Restricted remote deployment

The optional helper accepts source-only updates through a dedicated SSH key.
Run sudo sh bin/install-deploy-access.sh <public-key-file> <absolute-project-directory>
once as administrator. Keep the private key on the client, never in this repository.

The key permits no interactive shell or forwarding. The account has no Docker group
membership; sudo allows only the root-owned helper with deploy or status. Uploaded
regular Python/JS/CSS/HTML sources are path/size validated. Only the fixed three-service
stack is rebuilt. Dockerfile, dependencies, Compose, config and .env remain protected
administrator snapshots under /etc/rhythm-deploy.

Client: python bin/deploy.py --key <private-key-path> --host rhythm-deploy@<server>
Add --status for read-only container status. Backups and image tags are retained
for administrator cleanup; failed startup attempts rollback. Running containers
are not a substitute for functional verification.

Publisher updates can influence music inside their existing mounts. This is scoped
deployment authority, not protection against malicious application code.
Re-running the installer refreshes protected deployment configuration snapshots.

For an existing restricted deployment, an administrator can add the music drawer:
`sudo python3 app/configure_source.py --project <absolute-project-directory> --source /srv/media/music`
This backs up and updates only the protected web-service source mount. Source
permissions are unchanged. Re-deploy normally afterward to recreate containers.
The deployment key cannot execute this administrator command.

Revoke new SSH connections without stopping the app:
sudo mv /var/lib/rhythm-deploy-user/.ssh/authorized_keys /var/lib/rhythm-deploy-user/.ssh/authorized_keys.disabled
Remove /etc/sudoers.d/rhythm-deploy when retiring the account. Revocation does not
undo deployed code or terminate an already-running deployment.

## Development and repository hygiene

Python 3.12+, requirements.txt, and ffmpeg. Run:
python -m unittest discover -s tests
GitHub Actions runs tests on Linux. Demo data is available through app/demo.py;
never use actual music as test fixtures.

The repository excludes .env, keys, local runtimes/data, databases, logs, and
deployment backups. Review staged content before each push. Never put credentials
in repository URLs or commit messages. No license is selected yet; decide on
licensing before making this repository public.
