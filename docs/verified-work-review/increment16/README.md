# Verified-work review package — increment 16

Development review artifacts, not deployed runtime source. Published from an isolated checkout so Antigravity's dirty working tree is preserved. No merge, service restart, hold change or production activation is authorized by this package.

Start with [Antigravity handoff](ANTIGRAVITY_HANDOFF_PROMPT.md), [implementation and test evidence](INCREMENT_16_PROTECTED_CODE_VERIFICATION.md), and [pre-publication sync scan](SYNC_BEFORE_PUBLICATION.json). The handoff describes reconciliation, integration testing, normal branch publication and remote SHA verification.

`qwen-containment.patch` is cumulative and was checked against preserved original dirty-baseline bytes; **it is not guaranteed to apply to GitHub main**. `candidate-manifest.json` pins original and candidate file hashes. The patch includes runtime code, tests and integrated documentation. Reconcile in an isolated integration branch rather than blindly applying or overwriting either existing checkout.

417 selected regression tests passed; six separately enabled laptop scenarios passed across 18 actual confined executions; 55 fixed external checks passed. These were synthetic native database fixtures, not a real leased model coding canary. Source on main and Antigravity has not been replaced by this publication. Production remains gated.

The standalone protocol copy here accompanies the package; its links target the candidate documentation layout included in the cumulative patch.
