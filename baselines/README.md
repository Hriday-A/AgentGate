# Baselines

`smoke.json` is the reference result the CI eval gate compares pull requests against.
Refresh it deliberately, after a change that is meant to move the numbers:

```bash
python -m agentgate run --smoke --label baseline
cp runs/<run-id>/results.json baselines/smoke.json
```
