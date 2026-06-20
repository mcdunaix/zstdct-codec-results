#!/usr/bin/env python3
"""
Verify Android/device setup before running codewhale.
Checks: git auth, Telegram config, Python deps, SSH key.

Usage:
  python3 orchestration/verify_setup.py
"""

import sys
import subprocess
from pathlib import Path

ORCH_DIR = Path(__file__).parent
REPO_ROOT = ORCH_DIR.parent

def run_cmd(args, cwd=None):
    """Run command, return (success, output)."""
    try:
        result = subprocess.run(
            args,
            cwd=cwd or ORCH_DIR,
            capture_output=True,
            text=True,
            timeout=10
        )
        return result.returncode == 0, result.stdout.strip() + result.stderr.strip()
    except Exception as e:
        return False, str(e)

def check_python_deps():
    """Check required Python packages."""
    print("\n✓ Checking Python dependencies...", flush=True)
    deps = [("requests", "requests"), ("pyyaml", "yaml")]
    missing = []

    for dep_name, import_name in deps:
        try:
            __import__(import_name)
            print(f"  ✓ {dep_name}", flush=True)
        except ImportError:
            print(f"  ✗ {dep_name} MISSING", flush=True)
            missing.append(dep_name)

    if missing:
        print(f"\nInstall missing: pip install {' '.join(missing)}", flush=True)
        return False
    return True

def check_git_remote():
    """Check git remote is configured."""
    print("\n✓ Checking git remote...", flush=True)
    success, output = run_cmd(["git", "remote", "-v"], cwd=REPO_ROOT)
    if not success or "origin" not in output:
        print(f"  ✗ No git remote configured", flush=True)
        print(f"  Fix: git remote add origin <url>", flush=True)
        return False

    print(f"  ✓ Remote configured", flush=True)
    print(f"  {output.split(chr(10))[0]}", flush=True)
    return True

def check_git_auth():
    """Check git can authenticate with GitHub."""
    print("\n✓ Checking GitHub authentication...", flush=True)
    success, output = run_cmd(["git", "ls-remote", "origin", "HEAD"], cwd=REPO_ROOT)

    if not success:
        print(f"  ✗ GitHub auth failed", flush=True)
        print(f"\n  If using SSH key:", flush=True)
        print(f"    1. Generate: ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519", flush=True)
        print(f"    2. Test: ssh -T git@github.com", flush=True)
        print(f"    3. Add public key to GitHub: https://github.com/settings/keys", flush=True)
        print(f"\n  If using HTTPS token:", flush=True)
        print(f"    1. Create token: https://github.com/settings/tokens", flush=True)
        print(f"    2. git config credential.helper store", flush=True)
        print(f"    3. git push (will prompt for username/token)", flush=True)
        return False

    print(f"  ✓ GitHub auth successful", flush=True)
    return True

def check_git_config():
    """Check git user.name and user.email."""
    print("\n✓ Checking git config...", flush=True)
    success, name = run_cmd(["git", "config", "user.name"], cwd=REPO_ROOT)
    _, email = run_cmd(["git", "config", "user.email"], cwd=REPO_ROOT)

    if not success or not name:
        print(f"  ✗ git user.name not set", flush=True)
        print(f"    Fix: git config user.name 'Codewhale'", flush=True)
        return False

    if not email:
        print(f"  ✗ git user.email not set", flush=True)
        print(f"    Fix: git config user.email 'codewhale@mesh'", flush=True)
        return False

    print(f"  ✓ git config {name} <{email}>", flush=True)
    return True

def check_telegram_config():
    """Check Telegram .env file."""
    print("\n✓ Checking Telegram configuration...", flush=True)
    env_file = ORCH_DIR / ".env"

    if not env_file.exists():
        print(f"  ✗ .env file not found: {env_file}", flush=True)
        print(f"\n  Create .env with:", flush=True)
        print(f"    TELEGRAM_BOT_TOKEN=<token from @BotFather>", flush=True)
        print(f"    TELEGRAM_CHAT_ID=<your chat ID>", flush=True)
        print(f"\n  To get CHAT_ID:", flush=True)
        print(f"    1. Send /start to hermesticles_bot on Telegram", flush=True)
        print(f"    2. curl https://api.telegram.org/bot{'{TOKEN}'}/getUpdates", flush=True)
        print(f"    3. Find 'chat': {{'id': <CHAT_ID>}} in response", flush=True)
        return False

    try:
        env_dict = {}
        for line in env_file.read_text().strip().split("\n"):
            if "=" in line and not line.startswith("#"):
                key, val = line.split("=", 1)
                env_dict[key.strip()] = val.strip()

        token = env_dict.get("TELEGRAM_BOT_TOKEN", "")
        chat_id = env_dict.get("TELEGRAM_CHAT_ID", "")

        if not token or not chat_id:
            print(f"  ✗ .env incomplete", flush=True)
            if not token:
                print(f"    Missing: TELEGRAM_BOT_TOKEN", flush=True)
            if not chat_id:
                print(f"    Missing: TELEGRAM_CHAT_ID", flush=True)
            return False

        print(f"  ✓ .env configured", flush=True)
        return True
    except Exception as e:
        print(f"  ✗ Error reading .env: {e}", flush=True)
        return False

def check_queue_directory():
    """Check queue/ exists with jobs."""
    print("\n✓ Checking job queue...", flush=True)
    queue_dir = REPO_ROOT / "queue"

    if not queue_dir.exists():
        print(f"  ✗ queue/ directory not found", flush=True)
        print(f"    Fix: mkdir -p queue/", flush=True)
        return False

    jobs = list(queue_dir.glob("*.json"))
    if not jobs:
        print(f"  ⚠ queue/ is empty (no pending jobs)", flush=True)
        print(f"  Push jobs from main machine to get started", flush=True)
        return True  # Not a blocker

    print(f"  ✓ queue/ has {len(jobs)} jobs", flush=True)
    return True

def main():
    print("\n" + "="*60, flush=True)
    print("Codewhale Setup Verification", flush=True)
    print("="*60, flush=True)

    checks = [
        ("Python dependencies", check_python_deps),
        ("Git remote", check_git_remote),
        ("Git config", check_git_config),
        ("GitHub authentication", check_git_auth),
        ("Telegram config", check_telegram_config),
        ("Job queue", check_queue_directory),
    ]

    results = []
    for name, check_fn in checks:
        try:
            result = check_fn()
            results.append((name, result))
        except Exception as e:
            print(f"  ✗ Check error: {e}", flush=True)
            results.append((name, False))

    print("\n" + "="*60, flush=True)
    print("Summary", flush=True)
    print("="*60, flush=True)

    passed = sum(1 for _, result in results if result)
    total = len(results)

    for name, result in results:
        status = "✓" if result else "✗"
        print(f"{status} {name}", flush=True)

    print(f"\n{passed}/{total} checks passed", flush=True)

    if passed == total:
        print("\n✅ Setup complete! Ready to run codewhale.", flush=True)
        print("\nStart with:", flush=True)
        print("  python3 orchestration/codewhale_autonomous.py", flush=True)
        return 0
    else:
        print("\n❌ Setup incomplete. Fix issues above and retry.", flush=True)
        return 1

if __name__ == "__main__":
    sys.exit(main())
