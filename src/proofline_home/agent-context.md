# ProofLine Agent Context

ProofLine user resources are installed under `~/.proofline/`.

- Read governance contracts from `~/.proofline/contracts/`.
- Read the public managed `docs/operations/*.md` inventory from `~/.proofline/operations/`; wheel members under `proofline_home/operations/` and `manifest.yaml` path/SHA-256 records bind its exact bytes. Exact init reruns and initialized `proofline update --check` do not mutate it.
- Read artifact templates from `~/.proofline/templates/`.
- Read ProofLine skills from `~/.proofline/skills/`.
- Treat each project's `.proofline/lines/` and `.proofline/criteria/` as canonical artifacts, not harness resources.
- Use explicit artifact identities and preserve artifact history.
