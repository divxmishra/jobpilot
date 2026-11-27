# Backups & restoration

## What is backed up
- **Database**: a consistent snapshot taken with SQLite's online backup API, then
  checked with `PRAGMA integrity_check`. The live file is never copied directly.
- **Companion files**: everything under `data/users/<id>/`, which includes original
  uploads, `cv.md`, `profile.yml`, `portals.yml`, per-job `report.md`/`job.json`,
  generated resumes and reports. The audit JSONL logs are included too.
- **Excluded**: the local browser profile and temporary files.

## Where backups go
| Configuration | Destination |
|---|---|
| `BACKUP_S3_*` + `BACKUP_ENCRYPTION_KEY` set | S3-compatible object storage (Cloudflare R2 or Amazon S3), over HTTPS |
| Not set | A local folder next to the data directory (development only) |

Each backup is a tar.gz archive encrypted with **AES-256-GCM**. The key is derived from
`BACKUP_ENCRYPTION_KEY`. Object storage encrypts again at rest (R2 always does; on S3,
`x-amz-server-side-encryption: AES256` is requested). A JSON manifest stores
SHA-256 hashes of the archive and of the encrypted blob.

**Keep a copy of `BACKUP_ENCRYPTION_KEY` offline.** Without it, backups cannot be decrypted.

## When backups run
| Trigger | When |
|---|---|
| After changes (debounced) | `debounce_minutes` (default 5) after the last important change: profile, decisions, documents, settings, job evaluation |
| Scheduled | Every `interval_hours` (default 6) |
| Manual | Admin → Settings → Backups → **Run backup now** |

All three are configurable in Admin → Settings. The newest `retention` backups (default 14)
are kept and older ones are deleted.

**Maximum data-loss window (RPO):** changes made in the last `debounce_minutes`, plus
upload time, if the process stops before the debounced backup runs. If debounced
backups keep failing, the worst case is `interval_hours`. **Zero data loss is not guaranteed.**

## Verification
Every backup is read back from storage after upload. Its hash is compared and it is
decrypted. A backup is only marked `verified` if both checks pass. Results, sizes and
errors appear in Admin → Settings → Backups. Failures are also logged as errors.

## Automatic startup recovery
On every start, **before** migrations run:
1. Check the local database: `missing`, `corrupt` (fails `quick_check`), `empty` or `ok`.
2. If it is `ok`, nothing is restored. A valid database is never overwritten.
3. Otherwise, try backups from newest to oldest. Each one must pass its blob hash,
   decryption, archive hash, and an integrity check of the restored DB in a staging folder.
4. If the local DB is `empty` but has a change marker, backups older than that marker are skipped.
5. A corrupt database is kept aside as `jobpilot.corrupt-<timestamp>.db`.
6. The result is recorded in the audit log and shown in Admin → Settings → Backups.

## Manual restoration (disaster recovery)
1. Stop the service.
2. Download the newest `backups/<stamp>.jpb` and its `.json` manifest from the bucket.
3. Check `sha256(file) == manifest.blob_sha256`.
4. Decrypt and extract on a trusted machine:
   ```python
   import io, tarfile, os
   os.environ["BACKUP_ENCRYPTION_KEY"] = "<your key>"   # set locally, never commit
   from app.services.backups import decrypt
   archive = decrypt(open("STAMP.jpb", "rb").read())
   tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz").extractall("restored", filter="data")
   ```
5. Replace the data directory with `restored/` (or let startup recovery do it by
   removing the broken database), then start the service.

## Protecting backups
- Storage credentials exist only as environment variables on the server. They are never
  sent to the browser or written to logs.
- Use a bucket that is **private** (no public access). The credentials should allow only
  read, write, list and delete on that bucket.
- For protection against deletion, enable bucket versioning or object lock where your
  provider supports it. Note that this increases storage use.

## Important: backups are not persistent storage
Render's free plan has **no persistent disk**. Restarts and redeploys wipe the local
database, and startup recovery then restores the latest backup. Anything written after
that backup is lost. Real user data needs a persistent disk or managed database. See
`REQUIRED_USER_ACTIONS.md` for the options and costs.
