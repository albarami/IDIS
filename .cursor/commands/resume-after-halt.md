# resume-after-halt

Resume the system after a kill switch halt.

This command MUST be used only after:
- manual investigation
- log review
- confirmation that conditions are safe

It enforces verification before resuming.

## command
```bash
rm -f data/KILL_SWITCH && python scripts/bootstrap.py --verify
