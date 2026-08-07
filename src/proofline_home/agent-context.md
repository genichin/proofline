# ProofLine Agent Context

ProofLine user resources are installed under `~/.proofline/`.

- Read governance contracts from `~/.proofline/contracts/`.
- Read the public managed `docs/operations/*.md` inventory from `~/.proofline/operations/`; wheel members under `proofline_home/operations/` and `manifest.yaml` path/SHA-256 records bind its exact bytes. Exact init reruns and initialized `proofline update --check` do not mutate it.
- Read artifact templates from `~/.proofline/templates/`.
- Read ProofLine skills from `~/.proofline/skills/`.
- Treat only Line, Discovery, REQ, and AC under each project's `.proofline/` as current canonical artifacts, not harness resources.
- Preserve older out-of-scope `.proofline/` data as opaque retained data; do not create, interpret, or migrate it.
- Use explicit artifact identities. ProofLine validation does not guarantee Git implementation or delivery chronology.
