#!/bin/bash
# Setup script for Android deployment
# Run once on main machine to create GitHub repo structure

set -e

REPO_NAME="zstdct-codec-results"
GITHUB_USER="${1:-}"

if [ -z "$GITHUB_USER" ]; then
    echo "Usage: ./ANDROID_SETUP.sh <github_username>"
    echo "Example: ./ANDROID_SETUP.sh octocat"
    exit 1
fi

REPO_URL="https://github.com/$GITHUB_USER/$REPO_NAME.git"

echo "Setting up $REPO_NAME for $GITHUB_USER..."

# Create or navigate to repo
if [ -d "$REPO_NAME" ]; then
    cd "$REPO_NAME"
else
    mkdir "$REPO_NAME"
    cd "$REPO_NAME"
    git init
fi

# Create directory structure
mkdir -p queue results/codec_library orchestration/schemas

# Create .gitkeep to ensure directories are tracked
touch queue/.gitkeep
touch results/codec_library/.gitkeep

# Create initial queue jobs (warmup codecs)
for codec in brotli lz4 snappy zopfli; do
    case $codec in
        brotli) PRIORITY=1 ;;
        lz4) PRIORITY=2 ;;
        snappy) PRIORITY=3 ;;
        zopfli) PRIORITY=4 ;;
    esac

    for stage in m0 m1 m2 m3 m4; do
        cat > "queue/${codec}_${stage}.json" << EOF
{
  "id": "${codec}_${stage}",
  "codec": "$codec",
  "stage": "$stage",
  "priority": $PRIORITY,
  "spec_url": "",
  "notes": "Spoon-feed stage $stage for $codec"
}
EOF
    done
done

# Create README
cat > README.md << 'EOF'
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
EOF

# Create .env template
cat > orchestration/.env.example << 'EOF'
# Telegram bot credentials (get from @BotFather)
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_chat_id_here

# Git credentials (optional, use SSH key instead)
# GIT_USER=your_github_username
# GIT_TOKEN=your_github_token
EOF

# Create .gitignore
cat > .gitignore << 'EOF'
.env
*.log
__pycache__/
*.pyc
.DS_Store
*.swp
*.swo
EOF

# Initial commit
git add .
git config user.name "Codewhale-Setup" 2>/dev/null || true
git config user.email "codewhale@local" 2>/dev/null || true
git commit -m "Initial codec queue setup" || true

echo ""
echo "✅ Repo structure created"
echo ""
echo "Next steps:"
echo "1. Create GitHub repo: https://github.com/new"
echo "   Name: $REPO_NAME"
echo "2. Add remote:"
echo "   git remote add origin $REPO_URL"
echo "3. Push:"
echo "   git push -u origin main"
echo "4. On Android, clone and run codewhale_autonomous.py"
echo ""
echo "See orchestration/ANDROID_DEPLOYMENT.md for details."
