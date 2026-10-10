# SQLite execution storage

Core uses native `sqlite3` and schema version 1. Standalone runs use `runs/state.sqlite3`;
an embedding caller passes `database_path` explicitly to both `create_run` and supervised
`execution.generate`. CLI supports `video-report --runs ROOT --database DATABASE generate URL`.

`videos` stores canonical identity, including Bilibili part. `runs` stores the input snapshot,
pipeline stage, execution identity and result. `run_documents` stores ASR results/cloud tasks,
source events, canonical units, manifests and readable transcript. `reports` stores HTML and
its run/transcript relationship. `artifacts` registers media, images and report resources.
`run_statistics` and `execution_events` hold structured usage and stage facts. Original Pi
sessions, logs, media and PNG remain files. JSON/Markdown/HTML in each workspace are exports.

For an embedding operation, initialize storage first, then own one connection and
`BEGIN IMMEDIATE`. Call `create_run(..., database_path=path, db=db, export=False)` and pass that
same active connection to Core methods. These methods never commit or open another connection
when `db` is supplied. Commit before `store.export(run_id)` or worker dispatch. Never share a
connection across threads/processes. Standalone entrypoints own their short transactions.

Execution is claimed once per run. The supervisor passes its execution ID and database path
to the trusted Python Worker. Worker stages, results and cache completion are fenced by that
ID. Embedding callers own the final scheduling/settlement transaction; Core workers do not
finalize managed runs. A complete Pi result still requires `agent_settled`, final assistant
`stopReason == "stop"` and complete HTML. Database status does not replace these checks.

Download, ASR and transcript caches use SQL `PROCESSING / READY / FAILED` records. Identity
includes video/part, ASR settings, transcript/OCR settings, versions and actual subtitle text;
report mode does not change transcript identity. READY text contains its body in the same
transaction as READY. A waiter receives producer failure instead of retrying indefinitely.
The supervisor releases unfinished claims after the producer exits; startup/waiters check
dead producers too. Active producers keep ownership. Orphan workers are identified by their
run/execution command, stopped and confirmed exited before takeover. Old IDs cannot overwrite
the next producer. Media reuse additionally checks registered files; removed media does not
invalidate completed ASR/transcript text.

Legacy directory import is explicit and offline via `video_report_agent.migration.import_run`.
It retains IDs, source evidence and unknown fields and does not update database-owned runs
from compatibility exports. Online reuse/history never scans legacy directories. Missing
exports can be recreated with `Store(database_path, initialize=False).export(run_id)`.

Public-style task isolation exports only the current task's text/metadata/selected assets to
`.generation`; the database, WAL/SHM and backups stay outside that mount. Trusted Python owns
database access. Local host Pi has the same filesystem permissions as its caller and provides
no database access isolation. This migration does not change that Local boundary.
