# Maintenance

Use this checkout as the source of truth. Make collector changes here, run the relevant checks, update these notes, and commit the source before deploying. If a NAS-only repair is needed, reconcile it back into Git before the next deployment.

From the repository root, run `graphify update .` to refresh the local code graph. Open `graphify-out/GRAPH_REPORT.md` or `graphify-out/graph.html` when available. Generate graphs from this checkout only. The output is local and can be regenerated.

Open this documentation folder as an Obsidian vault. Keep private incident notes, site-specific paths, account details, and archives in private storage outside this repository.

See [[Architecture]].
