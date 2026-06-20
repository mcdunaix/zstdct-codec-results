# Codec Decomposition Sandbox

Orchestration system for parallel codec analysis via spoon-feed methodology.

**Agents**: Hermes (orchestrator) + Codewhale (worker, deepseek)

## Quick Start

### 1. Hermes: Start the orchestrator
```bash
python orchestration/hermes_orchestrator.py --mode daemon --interval 30
```

This polls the job queue and dispatches tasks to codewhale.

### 2. Codewhale: Poll for work
```bash
python orchestration/codewhale_interface.py --get-job
```

Returns a job spec. Codewhale runs M0-M4, then submits:
```bash
python orchestration/codewhale_interface.py --submit-job <job_id> --results <json_file>
```

## Job Lifecycle

```
pending → assigned → running → completed
                  ↓
              failed (retry logic)
```

## Structure

```
orchestration/
├── hermes_orchestrator.py      # Main coordinator loop
├── codewhale_interface.py      # Worker interface (codewhale calls this)
├── job_queue.yaml              # Job manifest (YAML)
├── schemas/
│   ├── codec_result.json       # Output schema (validation)
│   └── job_spec.json           # Job input schema
├── jobs/
│   ├── pending/                # Unstarted jobs
│   ├── assigned/               # Claimed by a worker
│   ├── running/                # In progress
│   └── completed/              # Done (pass/fail)
└── results/
    └── codec_library/          # Final results per codec
```

## Job Format

Each job is a `.json` file in `orchestration/jobs/pending/`:

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

## Result Format

Codewhale writes to `orchestration/results/codec_library/{codec}/{stage}.json`:

```json
{
  "job_id": "brotli_m0",
  "codec": "brotli",
  "stage": "m0",
  "status": "pass",
  "timestamp": "2026-06-19T14:23:00Z",
  "metrics": {
    "decoder_lines": 847,
    "test_cases": 200,
    "coverage": 0.98
  },
  "notes": "RFC7932 compliant, gates 523 real-world brotli files"
}
```

See `orchestration/schemas/codec_result.json` for full validation schema.

## Adding Codecs

Edit `orchestration/job_queue.yaml`:

```yaml
codecs:
  - name: brotli
    stages: [m0, m1, m2, m3, m4]
    priority: 1
    spec_url: "https://tools.ietf.org/html/rfc7932"
```

Run:
```bash
python orchestration/hermes_orchestrator.py --generate-jobs
```

This creates job files for all codecs.

## Monitoring

```bash
# Check queue status
python orchestration/hermes_orchestrator.py --status

# Watch hermes logs
tail -f orchestration/logs/hermes.log

# View results
ls orchestration/results/codec_library/
```

## Fault Handling

- **Job timeout** (>24h): marked failed, can be retried manually
- **Partial results**: hermes skips dependent stages until prerequisites pass
- **Worker crash**: hermes reclaims job from `assigned/`, requeus to `pending/`
