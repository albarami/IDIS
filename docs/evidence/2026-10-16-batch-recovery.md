# PUB-IMPL-01 recovery and provenance

Authorization: AUTH-IMPL-20261016 in `docs/go-live/RUN_STATE.md`; execution package `070fb57e4485e2ff6101cf7d7f26b60f38e9ecbe`.
Status: source preservation and private recovery, not product acceptance. CIR and GL-P01 changes are separate subsequent commits.

## Identities and privacy route

- Public base: `4594ecabc4d7b598b42a67bf999b894d510f0ec9`.
- Original local implementation tip: `445c3c844630ab232faea24d2e5ad02abdd1d3c6`; originals remain unchanged in the original worktree/branch.
- Sanitized preservation commit: `d713f7ae1add8175798a027df96fbca973ad29ea`, parent public base. Its tree `f2d4cb7f230bc6c3dac716ceba09d54510ddab71` is exactly the original tip tree: all2,776 path/mode/blob entries match. This preserves the source, not the original intermediate public history.
- Shared9-file overlay commit: `be5c53fd497d952db5db2894ffd8a3766245fd27`.
- Task8 public105-file overlay commit: `17c2b3b265b3c09121febd90c61a368e827a00ab`.
- Task9 public56-file overlay commit: `cdae32154e9f88dbe7e17420a9676e4590727b40`.
- All170 public overlay files retain their original raw SHA-256 values from BASELINE_MANIFEST; four private files are recovered separately. Use `git -c core.autocrlf=false clone` and verify raw bytes; do not silently normalize original CRLF/LF content. One preserved frozen fixture has an existing blank line at EOF; it was recorded, not reformatted.

The complete outgoing original history audit covered857 unique blobs,26 commit messages and287 tree objects (137,063,519 bytes of blob/commit content), plus174 overlay hashes. Bounded comparisons included private requirement strings, case labels and document filenames; flagged payloads/media provenance were inspected. This is exhaustive object/pattern coverage with targeted semantic review, not a claim of manual reading of every byte or universal secret detection.

Two restricted intermediate blobs must not be introduced by the publication branch:

| Technical path | Excluded intermediate blob | Cleared tip blob |
|---|---|---|
| `tests/test_real_example_run_harness.py` | `140f50c59cc838393132a170a2bd9416b0550640` | `c3b2847fd6b557698a925c4fccc3f4a8584bee27` |
| `tests/test_slice77_data_room_package.py` | `863098572e651f3881237cce7a6161012fdd4880` | `f119f7ac2c0a8a9d8dcc285e351a06354d44d8e4` |

These contain a private case label, deliberately not reproduced here. The first occurs in outgoing commits1–10; the second in6–10. Original commit `b76a8c8f9e1fd6e12a3825ee11f3a0e90cd34428` removes both. Related labels already exist on the previously public base; historical-main remediation is a separate owner decision. The sanitized candidate was made in a fresh public-base clone without the original26 commit objects or either restricted intermediate blob. Original local history was not rewritten or deleted.

## Original commit provenance

All26 identities below map to the exact original tip tree preserved by `d713f7a`; the mapping is not a claim that historical acceptance applies to new repairs.

```text
eeacce6251c8279b6993ec2580694bd69a8a5a9e
7bb49aa87830adde70d3ddbe09c857ce4f9925c2
1557a2c5cbfac411ddaf2ce703728432e0079311
b8b9adbafd34e7ccfe67c21e4525f713b3423ed7
47c19cf8dcf4569426fb75c32486346eb690f204
c42290038d30b287673af75deb9392d04105e84c
4a9e8f915debe42f88d69ef1fcd8f752d7931869
857f7719a2fafe2453ab6adbbc810c388f87be11
e375eae03cdbf2efd48aa01fe2fd37d6cd7971c7
f0d3becbb9becad0ff2632fda63726e18055aa91
b76a8c8f9e1fd6e12a3825ee11f3a0e90cd34428
b9280ed975b5fe8ca2022859ef807de311c0f3e8
28c67ccdfe58f6c71f3204b297ab9d77db006847
e7a7be6646497ff22bcdb6e5f61f5b9e05373c24
46b23d1b8235ea6c89cc75f79f2f71dee4f39fb6
9f3c42fbbb078e4c3ccf19de772bbe00b972701f
ea803d86f49ecc89a7847613f9dcc0f7a87a3b2a
11b7408d1d53657cb1f151b5ebc9d67813facfff
bb10c478323fce438de1dc101b0309cb1e03dfcd
ec47064b62b04a3a992bfa5fb2cf108de7ecf92c
f7fecfd0678aaa10573efaf92f8d17d5894940c2
a8249c2c3ece11984766fbbe81c22b2adc098451
a07c8ac02b3aeff3e8458de74a1b6d39ec2c19d7
1dfa02ecad91a6013e7e02725abfb5f2c8acef95
092b381680839c98a3bc115f6721062a34ed15c3
445c3c844630ab232faea24d2e5ad02abdd1d3c6
```

## Private exact-byte recovery

Repository: owner-controlled `albarami/IDIS-private-recovery`, verified `private=true`, `visibility=private` before and after upload. Recovery commit: `775aa7d0d88a1705c7c37805a0b2194eec436407`, branch `recovery/2026-10-16`. The7-file payload is exactly the four authorized artifacts, `.gitattributes` disabling text conversion, README and recovery manifest. A fresh authenticated clone reproduced all four raw SHA-256 values. No original workbook/case collection, credentials, profiles, workflow or executable was uploaded.

Artifact paths/hashes remain in BASELINE_MANIFEST and the private recovery manifest. Restore from that pinned private commit into the same relative paths of an authorized public checkout, validate before/after copy, and keep the private files ignored. Their contents must not enter public Git, Docker build contexts, logs or CI artifacts. New `.gitignore`/`.dockerignore` exclusions are publication safety metadata, separate from the preserved source commits. Default ignore admission was observed missing before the correction.

Private recovery alone is not full execution recovery. Original fund inputs, operator profiles/credentials, stores, source-document/artifact bytes and historical private execution receipts remain local/private and are not uploaded by this batch. No model/provider, activation, deployment or fund action is implied.

## Execution admission and open baseline failures

Static inspection finds38 migration modules, single head0038 with parent0037, no0039 file. Existing CI/bootstrap/test fixtures use `upgrade("head")`; revalidate the graph before every authorized disposable database run/publication. No0039 execution is authorized.

Full Task8 verification requires the private exact-byte overlay. Public CI without that overlay must remain failed/held, not skipped into a success. Private test failures can expose fund text through assertion rendering, so raw private-overlay logs stay controlled and only sanitized summaries are public. Restore no private files into a public Docker build context.

The preserved production image starts the strict launcher, while existing container CI supplies no required strict profile. This is an existing fail-closed admission/health mismatch, not permission to bypass admission, activate a contract or inject a real operator profile. Keep any resulting CI failure visible until an authorized correction is reviewed.

Local tests use isolated Python3.11, a scrubbed credential/profile environment and only explicitly disposable database/cache targets when needed. No operational database/Redis fallback, original fund collections or provider credentials are admitted. Development dependencies may download; test/model inference traffic is not authorized.

Independent source-preservation and private-payload reviews: PASS within those scopes. Public publication SHA/PR, full-overlay restoration and subsequent exact-head CI are recorded in the later checkpoint/PR receipt; none is claimed complete by this initial preservation record.
