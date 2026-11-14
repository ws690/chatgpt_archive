# Maintenance

Use this checkout as the source of truth. Make collector changes here, run the relevant checks, update these notes, and commit the source before deploying. If a NAS-only repair is needed, reconcile it back into Git before the next deployment.

From the repository root, run `graphify update .` to refresh the local code graph. Open `graphify-out/GRAPH_REPORT.md` or `graphify-out/graph.html` when available. Generate graphs from this checkout only. The output is local and can be regenerated.

Open this documentation folder as an Obsidian vault. Keep private incident notes, site-specific paths, account details, and archives in private storage outside this repository.

See [[Architecture]].

## Targeted project backfill

The runner accepts a `project` parameter with a URL-encoded ChatGPT project
pathname of the form `/g/g-p-…/project`. It inventories visible chat links on
that project page and attempts one chat per invocation. A project-specific
cursor is stored in the NAS browser profile independently of the daily
account cursor. Repeated invocations cycle through the visible chats and
reuse existing transcript and attachment checkpoints.

The visible chat count is a discovery result. Reconcile it with the project
in the browser before treating the backfill as complete. Project-level
files and instructions require separate validation. The importer polls
the staged downloads independently of transcript changes.

The browser run lock renews while the runner is responsive. Individual page operations remain bounded. An imported active-branch export baseline takes precedence over browser captures for the same indexed chat.

A later complete browser capture can advance an export-backed master if its
rendered transcript preserves the entire baseline as an exact prefix.
A partial capture or a representation mismatch keeps the verified baseline.

Transient chat-opening errors receive two retries. Skipped attachment names and reasons are recorded in the private NAS state file `extension-document-issues.jsonl`.

Outputs read errors are logged separately from transcript failures. The active run lease uses Chrome session storage, independent of persistent checkpoints.

Lease renewals and failed heartbeat classifications are recorded in the private NAS state file `extension-lease.jsonl`.

The run lock follows the owning Chrome runner tab. It has no elapsed-time
expiry or renewal write. Closing the runner tab releases its claim for the
next run.

When retrying a missing file, `download_source_unverified` means Chrome listed a
download with the expected name in the staging location, but the collector
could not verify its origin and chat referrer. `download_not_seen` means no
matching download was visible at the start deadline. Neither status counts
toward attachment coverage. The diagnostic records no download URLs.

A `renderer_timeout` means Chrome did not return a page-reading result. The collector keeps the verified history checkpoint for a later retry.
