# Local usage persistence operations

`JsonlUsageStore` is an opt-in, privacy-minimized local audit counter. It writes one bounded JSON
record per completed council run and never stores prompt/response text, provider/model names,
credentials, or raw task names.

```python
store = JsonlUsageStore(
    "./state/usage.jsonl",
    max_file_bytes=50 * 1024 * 1024,
    max_backup_files=3,
    lock_timeout_seconds=5,
    durable=True,
)
```

## Coordination and rotation

Each active path has a permanent sibling lock file, such as `usage.jsonl.lock`. Every append takes an
in-process mutex and then a bounded OS advisory lock on that file. This coordinates independent
`JsonlUsageStore` instances and processes on supported local Windows and POSIX filesystems. Lock
acquisition failure raises `TimeoutError`; `ConsensusEngine` redacts that as `write_failed` while
preserving the council result.

When the next complete record would exceed `max_file_bytes`:

- `max_backup_files=0` (default) fails without a partial append;
- a positive value rotates `usage.jsonl` to `.1`, `.1` to `.2`, and so on while holding the lock;
- the oldest generation beyond the configured count is replaced; and
- the record is written to a new owner-only active file.

Active, lock, and numbered backup paths are rejected when they are symbolic links. The library also
checks that opened active/lock paths are regular files. Keep the entire parent directory private;
Windows ACL policy remains an application/deployment responsibility.

## Boundaries and recovery

Advisory locking semantics vary on NFS, SMB, object-mounted, container-overlay, and distributed
filesystems. Do not assume this mechanism coordinates across hosts. Use an application-owned
`UsageStore` backed by a transactional shared service when multiple hosts write the same logical
stream.

`statistics()` streams only the active file and caps processed records. It does not silently include
archives, because doing so would make latency proportional to retention and complicate consistent
reads during rotation. Export or aggregate numbered backups explicitly before deletion.

Durable mode flushes and calls `fsync` for each record. This improves local crash durability but does
not guarantee remote storage durability, atomic directory metadata persistence, or survival of disk
failure. Monitor `write_failed` events, lock timeouts, active/backup sizes, free disk space, and
archive retention. Test restore/export procedures with the actual deployment filesystem.
