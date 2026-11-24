# Security policy

Security fixes target the latest 1.x release line. Use the current release or the matching tagged source when reporting a defect.

Report a vulnerability privately using **Security → Report a vulnerability** on the official GitHub repository once private reporting is enabled. Include the MalHash version, operating system, affected interface, and the smallest reproducible scenario. Use synthetic or redacted data. Coordinate a private transfer with a maintainer if the issue depends on sensitive evidence. Keep vulnerabilities and real samples out of public issues and pull requests.

## Handling untrusted files

MalHash opens inputs read-only and treats contents as data. It reads bounded blocks, validates file identity, rejects symbolic links and Windows reparse points, and checks for changes during processing. PE analysis uses bounded header validation and limits input to the import parser. Operation has no telemetry, remote lookup, execution, preview or automatic extraction.

Read-only access can still update the access timestamp on a writable filesystem. Use a read-only mount, write blocker or validated forensic image for evidence work. Where possible, scan a snapshot to avoid interference by concurrent processes. A hostile file system, compromised host, or process able to alter paths or content during a scan can exceed these checks. Run in an analysis VM with least privilege and store exported results outside the evidence tree.

CSV and TSV exports prefix potentially active formula-like text cells with an apostrophe. Original text is available in JSON. Treat raw filenames and exports as untrusted input when using them in scripts, reports or other tools.
