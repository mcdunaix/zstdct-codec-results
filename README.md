# Codec Decomposition Results

Distributed codec analysis via Git + Telegram + mesh network.

## Structure

- `queue/` — jobs to do (pull, work, commit result, push)
- `results/codec_library/{codec}/{stage}.json` — completed results

## Usage

### Push Job (Main Machine)

```bash
# Add job to queue/
echo '{"id":"codec_m0",...}' > queue/codec_m0.json
git add queue/
git commit -m "Queue codec M0"
git push
```

### Work on Device (Android)

```bash
python3 orchestration/codewhale_autonomous.py
```

Device pulls, works, commits result, pushes back.

### Monitor

```bash
git pull
cat results/codec_library/codec/m0.json | jq .
```

See `orchestration/ANDROID_DEPLOYMENT.md` for full setup.
test marker
