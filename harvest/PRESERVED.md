# Preserved from mission-control-v2

These files were copied out of `/home/walker/ventures/mission-control-v2/scripts/` on 2026-10-26
**before** that folder is deleted. They are preserved because they are working implementations that
the new build does not currently replace.

| File | Bytes | Why it is kept |
|---|---:|---|
| `ultron-wake.py` | 61,160 | The wake-word implementation. Substantial working code for a feature that is **parked, not deleted**. Would otherwise be lost — the folder has no git history. |
| `stt.py` | 2,080 | Existing speech-to-text entry point used by v2's `/api/stt` route. |
| `enroll-voice.py` | 3,804 | Voice enrolment helper. |
| `backup-local-state.sh` | 1,218 | The credential-excluding local recovery archive (SHA-256 + extraction dry-run). Useful pattern for the new repo. |

Integrity was verified by comparing SHA-256 of source and copy at the time of preservation.

## Scope note

Voice is **parked** per Sir's 2026-10-26 decision: DeepSeek-native realtime is closed (the model and
provider catalog expose no audio modality), and nothing voice-related is in the current build phases.
These files exist so the option stays open rather than being destroyed by the v2 deletion.

They are **not** wired into `ultron-prime` and should not be until voice is explicitly re-approved.

## Not preserved (deliberately)

- v2's application source (`src/`, `app/`) — being rewritten from zero; the *requirements* are
  extracted into `REQUIREMENTS-command.md` and `REQUIREMENTS-truth.md` in this directory.
- v2's 23 test files — read as requirement sources during the harvest; behaviour captured as invariants.
- `node_modules`, `.next`, build caches — regenerable.
- The run ledger history — it lives separately at `~/.local/share/ultron-mission-control/` and is
  unaffected by the folder deletion.