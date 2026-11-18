# Data Pack: Section G - Data & Operational Procedures

Collected on: 2026-11-16

## G-01: Key Storage & Lifecycle
* Database: `voice_platform.db` (SQLite).
* Key Table: `api_keys` storing:
  * `id`: Key identifier
  * `key_hash`: HMAC-SHA256 hash using `API_KEY_HASH_SECRET`
  * `role`: `"admin"` or `"caller"`
  * `scopes`: JSON array (e.g. `["stt", "llm", "tts", "voice_stream"]`)
  * `rate_limit_per_minute`, `total_quota`, `used_quota`
  * `expires_at`: Epoch timestamp or NULL
  * `is_active`: 1 (active) or 0 (revoked)
* Revocation: Immediate. When revoked, `is_active=0` fails authentication instantly on next request.
* Rotation: Supported via `POST /v1/admin/keys/{id}/rotate`.

## G-02: Data Stored
* Turn logs: Transcripts stored in `turns` table.
* Audio: Raw PCM/WAV chunks are processed in memory and discarded upon turn completion unless explicitly logged.
* `LOG_TRANSCRIPTS = False` by default in production config.

## G-03: Backup Procedures
* Scripts available:
  * `scripts/backup-db.sh`: Creates timestamped sqlite `.backup` in `data/backups/`.
  * `scripts/restore-db.sh`: Restores database atomically.
  * Verified working in automated regression test `test_m2_backup.py`.
