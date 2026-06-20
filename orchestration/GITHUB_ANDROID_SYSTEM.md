# GitHub + Android Distributed Codec System

End-to-end system for running codec decomposition on Android device via Tailscale mesh, with Telegram notifications and GitHub results.

## Architecture

```
Main Machine (you)
  ├─ GitHub: zstdct-codec-results
  │  ├─ queue/              ← push jobs here
  │  ├─ results/            ← pull results from here
  │  └─ orchestration/      ← scripts
  │
  └─ Local: optional
     ├─ hermes (5-min cron) ← status snapshots to Telegram
     └─ or just push jobs manually

Tailscale Mesh Network

Android Device (codewhale)
  ├─ Termux Python environment
  ├─ Clone: zstdct-codec-results
  ├─ Loop: codewhale_autonomous.py
  │        ├─ Pull latest
  │        ├─ Work on codec (M0-M4)
  │        ├─ Commit result
  │        ├─ Push to GitHub
  │        └─ Send Telegram notification
  └─ Results land in codec_library/
```

## Files

### New (for this system)

- `notifications.py` — Telegram integration (hermesticles_bot)
- `git_queue.py` — Git-based job queue (GitHub-backed)
- `codewhale_autonomous.py` — Main loop for Android
- `ANDROID_DEPLOYMENT.md` — Full setup guide
- `ANDROID_SETUP.sh` — One-time repo structure setup

### Existing (still useful)

- `hermes_orchestrator.py` — Optional status/monitoring on main machine
- `codewhale_interface.py` — File-based interface (can be archived)
- `spoonfeed.py` — Core M0-M4 methodology (in src/)

## Quick Start

### 1. Setup (Main Machine)

```bash
cd orchestration
bash ANDROID_SETUP.sh your_github_username
cd zstdct-codec-results

# Push to GitHub
git remote add origin https://github.com/your_username/zstdct-codec-results.git
git branch -M main
git push -u origin main
```

### 2. Setup (Android)

See `ANDROID_DEPLOYMENT.md` sections 1–5.

Summary:
```bash
# Termux
apt install -y python3 git openssh-client
pip install requests pyyaml

# Clone
git clone git@github.com:your_username/zstdct-codec-results.git
cd zstdct-codec-results

# Configure
git config user.name "Codewhale"
git config user.email "codewhale@mesh"

# Setup .env with Telegram credentials
cat > orchestration/.env << EOF
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
EOF
```

### 3. Push a Job (Main Machine)

```bash
cd zstdct-codec-results

# Manually create a job
echo '{"id":"brotli_m0","codec":"brotli","stage":"m0","priority":1}' > queue/brotli_m0.json

# Or use the pre-populated jobs
git add queue/brotli_m0.json
git commit -m "Queue brotli M0"
git push
```

### 4. Start Codewhale (Android)

```bash
cd ~/zstdct-codec-results
python3 orchestration/codewhale_autonomous.py
```

Or run in background:
```bash
nohup python3 orchestration/codewhale_autonomous.py > codewhale.log 2>&1 &
```

### 5. Monitor

**From Telegram**:
- Notifications appear in hermesticles_bot chat
- 🚀 job starts
- ✅ job completes
- 🎉 codec complete

**From main machine**:
```bash
cd zstdct-codec-results
git pull
git log --oneline | head   # See commits from Android
ls results/codec_library/  # See completed results
cat results/codec_library/brotli/m0.json | jq .
```

## Workflow Example

**T=0min**: Main machine pushes 20 jobs to GitHub
```bash
for codec in brotli lz4 snappy zopfli; do
  for stage in m0 m1 m2 m3 m4; do
    echo '{"id":"'$codec'_'$stage'","codec":"'$codec'","stage":"'$stage'","priority":1}' > queue/${codec}_${stage}.json
  done
done
git add queue/
git commit -m "Queue all 20 warmup jobs"
git push
```

**T=0.5min**: Android device (already running codewhale) sees jobs
```
[2026-06-19T15:30:45] Pulling...
Pending jobs: 20
Working on: brotli_m0
```

Telegram notification:
```
🚀 BROTLI M0
brotli_m0
```

**T=4hours**: First codec M0 completes
```
✅ BROTLI M0 (pass)
📝 847 lines | 200 tests | 98% coverage
```

Result auto-committed + pushed:
```
commit abc1234
Author: Codewhale <codewhale@mesh>
    Complete brotli_m0: pass
    
    • results/codec_library/brotli/m0.json
```

**T=days**: All codecs decomposed
```
🎉 BROTLI COMPLETE
✓ All 5 stages passed

🎉 LZ4 COMPLETE
✓ All 5 stages passed

... (snappy, zopfli)
```

Main machine pulls and has full library:
```bash
git pull
tree results/codec_library/
# results/codec_library/
# ├── brotli/
# │   ├── m0.json
# │   ├── m1.json
# │   ├── m2.json
# │   ├── m3.json
# │   └── m4.json
# ├── lz4/
# │   ├── m0.json
# │   ├── m1.json
# │   └── ...
# ...
```

## Key Properties

✅ **Distributed**: Android device autonomous; no server needed  
✅ **Durable**: Git is the state system; results are persistent  
✅ **Observable**: Telegram notifications in real-time  
✅ **Auditable**: Commit log proves what happened when  
✅ **Scalable**: Multiple devices can clone and work in parallel  
✅ **Offline-capable**: Device can work without internet, sync when connected  
✅ **No hallucination**: All state external (GitHub), not in agent context  

## Telegram Notifications

The hermesticles_bot will send messages to your chat:

```
🚀 CODEC M0
codec_m0

✅ CODEC M0 (pass)
📝 847 lines | 200 tests | 98% coverage

❌ CODEC M0 (fail)
Error message here

🎉 CODEC COMPLETE
✓ All 5 stages passed
```

## Scaling: Multiple Devices

If you have multiple Android devices, they can work in parallel:

```
Main Machine (GitHub)
    ↓
    ├─→ Android Device 1: Queue pull, brotli + lz4
    ├─→ Android Device 2: Queue pull, snappy + zopfli
    └─→ Android Device 3: Queue pull, more codecs

Each device:
  - Pulls queue/
  - Works on codec
  - Commits result
  - Pushes results/
  - Sends Telegram
```

No coordination needed; Git handles concurrent writes (rare conflicts; simple to resolve).

## Implementing M0–M4

In `codewhale_autonomous.py`, the `work_on_job()` function is where the actual codec decomposition happens.

Currently it's a placeholder. To make it work:

1. Import the spoon_feed methodology from `src/zstdct/spoonfeed.py`
2. Implement M0–M4 for the requested codec
3. Return a result dict with:
   ```python
   {
     "job_id": "codec_stage",
     "codec": "codec_name",
     "stage": "m0|m1|m2|m3|m4",
     "status": "pass" or "fail",
     "timestamp": "ISO8601",
     "metrics": {
       "decoder_lines": ...,
       "test_cases": ...,
       "coverage": 0.0–1.0,
       ...
     },
     "notes": "narrative notes"
   }
   ```

See `src/zstdct/` for reference implementations (bzip2, zstd, LZMA, gzip).

## Troubleshooting

**Android can't push to GitHub**:
- Verify SSH key: `ssh -T git@github.com`
- Check git remote: `git remote -v`
- Manually test push: `git push origin main`

**Telegram not sending**:
- Verify .env: `cat orchestration/.env`
- Test token: `curl https://api.telegram.org/bot{TOKEN}/getMe`
- Restart codewhale loop

**Job stuck**:
- Kill: `pkill -f codewhale_autonomous`
- Check log: `tail -f codewhale.log`
- Restart

**Git conflicts**:
- Unlikely (different devices, different jobs)
- If it happens: `git pull`, resolve, `git push`

## Future: Hermes Status Monitor

The file-based hermes system (`hermes_orchestrator.py`) can optionally run on main machine to periodically report status:

```bash
# Crontab (main machine)
*/30 * * * * python orchestration/hermes_orchestrator.py --poll >> orchestration/logs/cron.log 2>&1
```

This would:
- Pull latest from GitHub
- Count pending/completed/failed jobs
- Send Telegram status snapshot
- Log to checkpoints/

But it's optional; codewhale already sends per-job notifications.

---

**Next**: Implement the actual M0–M4 work in `codewhale_autonomous.py`, then deploy to Android.
