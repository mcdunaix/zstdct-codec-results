# Quickstart: Hermes + Codewhale

Stateless orchestration system (safe for long-running agents).

## Setup (one-time)

```bash
cd orchestration/

# Create directory structure
mkdir -p jobs/{pending,assigned,running,completed} results/codec_library checkpoints

# Install dependencies (Python 3.9+)
pip install pyyaml
```

## 1. Generate Jobs

```bash
python hermes_orchestrator.py --generate-jobs
```

This reads `job_queue.yaml` and creates JSON files in `jobs/pending/`.

Verify:
```bash
ls jobs/pending/ | head
# Should show: brotli_m0.json, brotli_m1.json, ..., lz4_m0.json, etc.
```

## 2. Start Hermes Cron (Stateless Polling)

Add to crontab (runs every 5 minutes, independently):
```bash
*/5 * * * * cd /path/to/orchestration && python hermes_orchestrator.py --poll >> logs/cron.log 2>&1
```

Or run manually to test:
```bash
python hermes_orchestrator.py --poll
```

Output (JSON to stdout):
```json
{
  "timestamp": "2026-06-19T15:30:45.123456",
  "pending_count": 18,
  "assigned_count": 1,
  "running_count": 0,
  "completed_count": 1,
  "reclaimed_count": 0,
  "reclaimed": []
}
```

Each poll writes a checkpoint to `checkpoints/` (audit trail).

Check status anytime (read-only):
```bash
python hermes_orchestrator.py --status
```

## 3. Codewhale: Request a Job

```bash
python codewhale_interface.py --get-job
```

Output (JSON):
```json
{
  "id": "brotli_m0",
  "codec": "brotli",
  "stage": "m0",
  "priority": 1,
  "spec_url": "https://tools.ietf.org/html/rfc7932",
  "notes": "M0: byte-exact RFC7932 decoder"
}
```

The job is moved from `pending/` → `assigned/`.

## 4. Codewhale: Do the Work

Implement the spoon-feed stage. See `CODEWHALE_GUIDE.md` for details.

Write results to a JSON file:
```json
{
  "job_id": "brotli_m0",
  "codec": "brotli",
  "stage": "m0",
  "status": "pass",
  "timestamp": "2026-06-19T15:35:00Z",
  "metrics": {
    "decoder_lines": 847,
    "test_cases": 200,
    "coverage": 0.98
  },
  "notes": "RFC7932 compliant"
}
```

## 5. Codewhale: Submit Results

```bash
python codewhale_interface.py --submit-job brotli_m0 --result-file /tmp/brotli_m0.json
```

Output:
```json
{
  "status": "ok",
  "job_id": "brotli_m0",
  "result_file": "/path/to/results/codec_library/brotli/m0.json"
}
```

Job moves from `assigned/` → `completed/`, result lands in `codec_library/`.

## 6. Loop (Codewhale Autonomous)

Codewhale can loop autonomously:

```bash
#!/bin/bash
# codewhale_loop.sh
while true; do
  RESULT=$(python codewhale_interface.py --get-job)
  
  if echo "$RESULT" | grep -q "no_pending_jobs"; then
    echo "All jobs done"
    break
  fi
  
  JOB_ID=$(echo "$RESULT" | python -c "import sys, json; print(json.load(sys.stdin)['id'])")
  CODEC=$(echo "$RESULT" | python -c "import sys, json; print(json.load(sys.stdin)['codec'])")
  STAGE=$(echo "$RESULT" | python -c "import sys, json; print(json.load(sys.stdin)['stage'])")
  
  echo "Running: $JOB_ID ($CODEC $STAGE)"
  
  # Do the work (implement M0-M4)
  # Write result.json
  
  python codewhale_interface.py --submit-job "$JOB_ID" --result-file result.json
  
  sleep 1  # Brief pause between jobs
done
```

## Monitoring

```bash
# Check queue status (read-only, safe anytime)
python hermes_orchestrator.py --status

# Watch cron polls (JSON events)
tail -f logs/cron.log

# View audit trail (one per poll)
ls -lt checkpoints/ | head -5

# See completed results
ls -la results/codec_library/brotli/
cat results/codec_library/brotli/m0.json | jq .
```

## Key Properties (Context-Safe)

✅ **Stateless**: Each hermes invocation is independent (no accumulation)  
✅ **Durable**: All state on disk; safe to restart  
✅ **Auditable**: Checkpoints prove what happened when  
✅ **No hallucination**: JSON contracts enforce reality  
✅ **Scalable**: 1000 jobs won't blow up agent context  

## Adding Codecs

Edit `job_queue.yaml`:

```yaml
  - name: my_new_codec
    stages: [m0, m1, m2, m3, m4]
    priority: 5
    spec_url: "https://..."
```

Regenerate:
```bash
python hermes_orchestrator.py --generate-jobs
```

New jobs appear in `pending/` immediately.

## Cleanup / Reset

Start over (keep completed results):
```bash
rm -rf jobs/assigned jobs/running jobs/pending
python hermes_orchestrator.py --generate-jobs
```

Wipe everything:
```bash
rm -rf jobs results checkpoints
```

## Troubleshooting

**Hermes can't find jobs?**
```bash
python hermes_orchestrator.py --status
ls jobs/pending/ | wc -l
```

**Job stuck in assigned?**
Hermes auto-reclaims after 24h. Force manually:
```bash
python hermes_orchestrator.py --reclaim
```

**Results not appearing?**
- Verify result JSON: `python -m json.tool result.json`
- Check `completed/` directory for the job record
- Grep checkpoints for errors: `grep -i error checkpoints/*.json`

**Context window risk?**
Hermes runs as isolated 5-min invocations. No context accumulation. Each poll writes to disk, exits, starts fresh. Safe indefinitely.

---

**Next**: See `CODEWHALE_GUIDE.md` for what to implement at each stage.
