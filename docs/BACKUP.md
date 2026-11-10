# Backup and restore

`backend/scripts/backup.py` writes an encrypted PostgreSQL dump and restores it into a new database.
It needs `pg_dump`, `pg_restore` and `createdb` on the path (the Docker image ships them) and
`LUMELIA_ENCRYPTION_KEY` in the environment.

```bash
cd backend
uv run python scripts/backup.py backup /secure/path/lumelia-2026-01-31.dump.fernet
uv run python scripts/backup.py restore /secure/path/lumelia-2026-01-31.dump.fernet --database lumelia_restore_check
```

An administrator can also download the same encrypted file from Settings
(`POST /api/backups`), at most once every five minutes.

## Guarantees

- The dump is in PostgreSQL custom format and encrypted with Fernet before it reaches the target
  path. The file is created exclusively: an existing backup is never overwritten.
- Restore only accepts a database named `lumelia_restore_*` and creates it. If the database exists
  the command fails, so a restore cannot overwrite live data. Promoting a restored database is a
  deliberate, separate step.
- The key that encrypts the backup also encrypts provider credentials, mailbox passwords and call
  audio. Losing it makes both the backup and those values unreadable: keep it outside the database
  and outside the backup location.

## Restore check

A backup is only useful once it was restored. After `restore`, point a second instance at the
restored database (`LUMELIA_DATABASE_URL`) with the same key and check that a stored credential
still decrypts, for example by opening a mailbox in Settings.

Encrypted call audio lives outside the database (local directory or bucket) and needs its own copy.
