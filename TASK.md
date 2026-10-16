# Active engineering tasks

Authority and acceptance: [RUN_STATE](docs/go-live/RUN_STATE.md), AUTH-CONTINUE-20261016.
Packet scope: [IMPLEMENTATION_BACKLOG](docs/go-live/IMPLEMENTATION_BACKLOG.md).

- [x] Reconcile starting implementation checkout and remote at `c0aeca02ec5fe381fda258426ad17ccd5c5e50c6`; clean before coordination edits.
- [x] Verify private recovery access/visibility, pinned commit and four raw artifact hashes; retain ignore/Docker exclusions and persistent `core.autocrlf=false`.
- [x] Record Salim's continuous engineering grant; preserve historical grants.
- [x] Verify Claude subscription CLI repository access: pre-upgrade CLI2.1.63 probe resolved `claude-sonnet-4-6`; updated CLI2.1.301 verified `claude-opus-5-5`. Re-resolve Sonnet when selected for a routine review.
- [x] Verify Sol 6.1 identity and supported implementation invocation: ACP `authenticate` succeeded; actual session `chief-banjo` selected `gpt-6-1-sol-xhigh`.
- [ ] Independently review and publish the coordination/recovery checkpoint.
- [ ] Complete bounded CI-baseline corrections with Sol and independent Claude review.
- [ ] Execute GL-P02, then GL-P03 and subsequent dependency-ready packets.

## Discovered During Work

- [x] Qualify a fresh `.venv` against the existing CIR-04 constraints: all138 distributions match, `pip check` passes and the234 recovery/materialization consumer tests pass. Preserve the borrowed SDK1.15.0 results separately; product repairs still require their own qualified checks.
- [x] Resolve implementation access through the supported ACP host-authentication method; no model substitution. Standalone command authentication remains a tool limitation.
- [x] Restore the historical v27 candidate and verify its raw hash against both oracles. The materialization module passed19 tests, of which only the LF/newline check reads the restored file; consumer verification and independent review are tracked separately.
- [ ] Reconcile hash-owned Task8 lint and intentional negative-fixture scanner findings through legitimate ownership/successor review.
- [ ] Prepare disposable CI roles with migration0038's exact posture and no memberships; preserve migration0039 hold.
- [ ] Diagnose legacy PostgreSQL fixtures against actual current schema and behavior.
- [ ] Correct the independently reproduced cold-import cycle: Sol owns isolated CIR-04D worktree and candidate initializers/relevant existing tests; review and integration pending.
- [ ] Preserve successful image construction versus failed strict profile admission/health as separate results.
