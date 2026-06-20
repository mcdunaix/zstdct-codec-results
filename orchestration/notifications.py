#!/usr/bin/env python3
"""
Telegram notifications via hermesticles_bot.

Set environment variables:
  TELEGRAM_BOT_TOKEN=<token from @BotFather>
  TELEGRAM_CHAT_ID=<your chat ID>

Or create a .env file in orchestration/:
  TELEGRAM_BOT_TOKEN=...
  TELEGRAM_CHAT_ID=...
"""

import os
import requests
from pathlib import Path

# Load from .env if it exists
ENV_FILE = Path(__file__).parent / ".env"
if ENV_FILE.exists():
    for line in ENV_FILE.read_text().strip().split("\n"):
        if "=" in line and not line.startswith("#"):
            key, val = line.split("=", 1)
            os.environ.setdefault(key.strip(), val.strip())

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


def send_message(text):
    """Send a message via hermesticles_bot."""
    if not BOT_TOKEN or not CHAT_ID:
        print(f"Warning: Telegram not configured (token={bool(BOT_TOKEN)}, chat_id={bool(CHAT_ID)})", flush=True)
        return False

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}

    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            return True
        else:
            print(f"Telegram error: {resp.status_code} {resp.text}", flush=True)
            return False
    except Exception as e:
        print(f"Telegram send failed: {e}", flush=True)
        return False


def notify_job_start(job_id, codec, stage):
    """Notify when starting a job."""
    msg = f"🚀 <b>{codec.upper()}</b> {stage.upper()}\n<code>{job_id}</code>"
    send_message(msg)


def notify_job_complete(job_id, codec, stage, status, metrics=None):
    """Notify when a job completes."""
    emoji = "✅" if status == "pass" else "❌"
    msg = f"{emoji} <b>{codec.upper()}</b> {stage.upper()} <code>({status})</code>"

    if metrics:
        if "decoder_lines" in metrics:
            msg += f"\n📝 {metrics['decoder_lines']} lines"
        if "test_cases" in metrics:
            msg += f" | {metrics['test_cases']} tests"
        if "coverage" in metrics:
            msg += f" | {metrics['coverage']:.0%} coverage"

    send_message(msg)


def notify_codec_complete(codec, stages_passed):
    """Notify when entire codec is complete."""
    msg = f"🎉 <b>{codec.upper()}</b> COMPLETE\n✓ All {len(stages_passed)} stages passed"
    send_message(msg)


def notify_status(pending_count, assigned_count, completed_count):
    """Send a status snapshot."""
    msg = f"📊 Queue Status\n⏳ Pending: {pending_count}\n🔄 Working: {assigned_count}\n✅ Done: {completed_count}"
    send_message(msg)


def notify_error(job_id, error_msg):
    """Notify on job failure."""
    msg = f"⚠️ <b>Job Failed</b>\n<code>{job_id}</code>\n{error_msg[:200]}"
    send_message(msg)
