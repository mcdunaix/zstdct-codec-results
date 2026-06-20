# Android Deployment (Tailscale Mesh)

Run codec decomposition on Android device via hermesticles Telegram bot.

## Prerequisites

- Android device on Tailscale mesh network
- Termux or similar Python environment
- GitHub repo: `zstdct-codec-results` (with queue/ and results/ directories)
- Telegram bot: `hermesticles_bot` (already configured)

## Setup (One-Time)

### 1. Install on Android (Termux)

```bash
# Open Termux (Android terminal app)
apt update
apt install -y python3 git openssh-client curl

# Verify
python3 --version
git --version
```

### 2. Clone Repo

```bash
cd ~
git clone https://github.com/yourusername/zstdct-codec-results.git
cd zstdct-codec-results

# Configure git (for commits)
git config user.name "Codewhale"
git config user.email "codewhale@tailscale"
```

### 3. Setup SSH (for git push without password)

On your main machine, generate a key:
```bash
ssh-keygen -t ed25519 -f ~/.ssh/android_github -N ""
cat ~/.ssh/android_github.pub  # Copy this
```

Add public key to GitHub:
- Go to https://github.com/settings/keys
- New SSH Key
- Paste the public key

Transfer private key to Android:
```bash
# On main machine
adb push ~/.ssh/android_github /sdcard/Download/

# On Termux
mkdir -p ~/.ssh
cp /sdcard/Download/android_github ~/.ssh/
chmod 600 ~/.ssh/android_github

# Test
ssh -T git@github.com  # Should say "Hi username"
```

Configure git to use SSH:
```bash
git remote set-url origin git@github.com:yourusername/zstdct-codec-results.git
```

### 4. Setup Telegram Bot

On your main machine, find your bot credentials:
```bash
# You already have hermesticles_bot
# Get the token from where you saved it
# Get your chat ID: send /start to the bot, then:
curl https://api.telegram.org/bot{TOKEN}/getUpdates | jq '.result[0].message.chat.id'
```

On Android (Termux):
```bash
cd zstdct-codec-results/orchestration
cat > .env << EOF
TELEGRAM_BOT_TOKEN={your_bot_token}
TELEGRAM_CHAT_ID={your_chat_id}
EOF
chmod 600 .env
```

### 5. Install Python Dependencies

```bash
pip install requests pyyaml

# Verify
python3 -c "import requests; print('OK')"
```

### 6. Verify Setup

Before running codewhale, verify all prerequisites are met:

```bash
cd ~/zstdct-codec-results
python3 orchestration/verify_setup.py
```

This checks:
- Python dependencies installed
- Git remote configured
- Git user.name/email set
- GitHub SSH authentication works
- Telegram .env is configured
- Job queue exists

All checks must pass before starting codewhale.

## Running

### Test Queue (Optional)

Create a test job:
```bash
cd ~/zstdct-codec-results

# Create a test job in queue/
mkdir -p queue
echo '{"id":"test_m0","codec":"test","stage":"m0","priority":1}' > queue/test_m0.json

git add queue/
git commit -m "Add test job"
git push
```

### Start Codewhale Loop

```bash
cd ~/zstdct-codec-results
python3 orchestration/codewhale_autonomous.py
```

Output:
```
Codewhale autonomous started
Repo: /home/user/zstdct-codec-results
[2026-06-19T15:30:45...] Pulling...
Pending jobs: 1
Working on: brotli_m0
Codec: brotli, Stage: m0
...
```

### Run in Background (Persistent)

```bash
# Option 1: nohup
cd ~/zstdct-codec-results
nohup python3 orchestration/codewhale_autonomous.py > codewhale.log 2>&1 &

# Option 2: Termux service (create script)
cat > ~/.termux/task-codewhale.sh << 'EOF'
#!/bin/bash
cd ~/zstdct-codec-results
exec python3 orchestration/codewhale_autonomous.py
EOF
chmod +x ~/.termux/task-codewhale.sh

# Then use Termux boot or scheduler
```

### Monitor

From Android:
```bash
# Watch log
tail -f ~/zstdct-codec-results/codewhale.log

# Check status
cd ~/zstdct-codec-results
python3 orchestration/git_queue.py  # Shows pending jobs

# Check Telegram
# Look for messages from hermesticles_bot
```

From main machine:
```bash
cd path/to/zstdct-codec-results
git log --oneline | head  # See commits from Android
ls results/codec_library/*/  # See completed results
```

## Workflow

1. **Push job to GitHub** (main machine):
   ```bash
   echo '{"id":"brotli_m0","codec":"brotli","stage":"m0","priority":1}' > queue/brotli_m0.json
   git add queue/
   git commit -m "Queue brotli M0"
   git push
   ```

2. **Android pulls and works**:
   - Codewhale sees new job
   - Sends Telegram: 🚀 BROTLI M0
   - Runs codec decomposition (M0)
   - Saves result to `results/codec_library/brotli/m0.json`
   - Commits and pushes to GitHub
   - Sends Telegram: ✅ BROTLI M0 (pass)

3. **Main machine sees results**:
   ```bash
   git pull
   cat results/codec_library/brotli/m0.json | jq .
   ```

## Telegram Notifications

**Job starting**:
```
🚀 BROTLI M0
brotli_m0
```

**Job complete**:
```
✅ BROTLI M0 (pass)
📝 847 lines | 200 tests | 98% coverage
```

**Codec complete**:
```
🎉 BROTLI COMPLETE
✓ All 5 stages passed
```

**Error**:
```
⚠️ Job Failed
brotli_m0
[error message]
```

## Troubleshooting

**"git pull" fails**:
```bash
# Check internet
ping google.com

# Check SSH key
ssh -T git@github.com

# Check remote
git remote -v
```

**"Telegram not configured"**:
- Verify .env file exists: `cat orchestration/.env`
- Verify TOKEN and CHAT_ID are set
- Restart codewhale loop

**Job gets stuck**:
- Check codewhale log: `tail -f codewhale.log`
- Kill and restart: `pkill -f codewhale_autonomous`

**Results not pushing**:
- Verify SSH key works: `ssh -T git@github.com`
- Check git status: `git status`
- Try manual push: `git push origin main`

## Scaling to Multiple Codecs

Queue jobs for multiple codecs:
```bash
for codec in brotli lz4 snappy zopfli; do
  for stage in m0 m1 m2 m3 m4; do
    echo '{"id":"'$codec'_'$stage'","codec":"'$codec'","stage":"'$stage'","priority":1}' > queue/${codec}_${stage}.json
  done
done

git add queue/
git commit -m "Queue 20 codec jobs"
git push
```

Codewhale will work through them sequentially, sending a Telegram notification per job.

## Performance Notes

- **M0 (decoder)**: 4–24 hours per codec (complexity-dependent)
- **M1–M4**: 2–12 hours per codec
- **Total**: 1–5 days per codec on a single device

Codecs can run in parallel on multiple Android devices (each pulls, works, pushes independently).
