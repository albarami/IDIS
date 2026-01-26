# harvest-daily

Run the daily winner harvesting process to build and update the Black Book.

This command:
- finds recent 10x+ tokens
- reverse-searches early buyers
- traces funding relationships
- updates the wallet graph

This command does NOT execute trades.

## command
```bash
python scripts/harvester.py
