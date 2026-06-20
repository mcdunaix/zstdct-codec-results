#!/usr/bin/env python3
"""
decomposer.py — the generate->validate->retry step that lives INSIDE the loop.

This is the piece that closes the 2026-06-20 gap: instead of running a pre-built
verifier, the worker GENERATES the decoder itself when one is missing. Generation
is delegated to Codewhale (the DeepSeek-backed coding agent) via its CLI —
`codewhale exec --auto` — which writes the decoder file with its own credentials.
This module never embeds an HTTP client and never touches an API key.

The flow for a hard-gated (codec, stage):
  1. ask Codewhale to write generated/<codec>_decoder.py
  2. validate it against the trusted byte-exact oracle (oracle.py)
  3. pass -> done (the loop registers the trusted verifier + records a gated result)
     fail -> feed the measured failure back, retry up to K times
  4. give up honestly after K tries or a wall-clock budget — never a fake pass.

Only M0 (decoder) and M2 (encoder) have a hard byte-exact oracle, so only those
are generated here. M1/M3/M4 lack a single hard oracle; the loop reports them
honestly (not_implemented/partial) rather than letting a machine assert a pass.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import oracle
import generated

ORCH_DIR = Path(__file__).resolve().parent
REPO_ROOT = ORCH_DIR.parent

# --- config (env-overridable; sensible defaults) ---------------------------
MAX_RETRIES = int(os.environ.get("DECOMP_MAX_RETRIES", "3"))     # attempts per stage
ATTEMPT_TIMEOUT_S = int(os.environ.get("DECOMP_ATTEMPT_TIMEOUT_S", "900"))   # per agent call
CODEC_BUDGET_S = int(os.environ.get("DECOMP_CODEC_BUDGET_S", "3600"))        # runaway kill switch
DRY_RUN = os.environ.get("DECOMP_DRY_RUN", "") not in ("", "0", "false")


# --- generation agent: which CLI writes the decoder --------------------------
# Two supported agent CLIs, each a DeepSeek-backed coder holding its OWN key (we
# hold none). They differ only in invocation. We auto-detect, preferring hermes
# (the edge/tablet agent — codewhale isn't installed there). Pin with GEN_AGENT;
# force a model with GEN_AGENT_MODEL (else the agent's own configured default).
GEN_AGENT_MODEL = os.environ.get("GEN_AGENT_MODEL", "")

def _hermes_argv(prompt, model):
    # -z = one-shot, approvals auto-bypassed, tools/memory loaded; --yolo +
    # --accept-hooks make it fully non-interactive (no TTY prompt can hang it).
    argv = ["hermes", "-z", prompt, "--yolo", "--accept-hooks"]
    return argv + (["-m", model] if model else [])

def _codewhale_argv(prompt, model):
    argv = ["codewhale", "exec", "--auto"]
    argv += (["--model", model] if model else [])
    return argv + [prompt]

_AGENTS = [("hermes", _hermes_argv), ("codewhale", _codewhale_argv)]  # priority order


def selected_agent():
    """(name, argv_builder) for the generation agent, or (None, None). GEN_AGENT
    pins a choice; otherwise the first agent on PATH wins (hermes first)."""
    pin = os.environ.get("GEN_AGENT", "").strip()
    for name, builder in _AGENTS:
        if (not pin or pin == name) and shutil.which(name):
            return name, builder
    return None, None


def agent_available() -> bool:
    return selected_agent()[0] is not None


def agent_name() -> str:
    return selected_agent()[0] or "none"

# Stages the decomposer will generate (have a byte-exact oracle).
GENERATABLE = ("m0", "m2")

# The trusted code that decides pass/fail. The codegen agent must NEVER alter it.
# We snapshot these before invoking Codewhale and restore them after, so even an
# over-eager --auto agent cannot weaken the oracle or gate. (Validation also runs
# against the in-memory modules imported at startup, so this guards the on-disk
# artifact and any later process that re-imports them.)
TRUSTED_FILES = [ORCH_DIR / n for n in
                 ("oracle.py", "verify.py", "generated.py", "provenance.py")]


def _snapshot_trusted() -> dict:
    snap = {}
    for p in TRUSTED_FILES:
        try:
            snap[p] = p.read_bytes()
        except OSError:
            pass
    return snap


def _restore_trusted(snap: dict) -> list[str]:
    """Restore any trusted file the agent changed. Returns the names it touched."""
    touched = []
    for p, original in snap.items():
        try:
            if p.read_bytes() != original:
                p.write_bytes(original)
                touched.append(p.name)
        except OSError:
            p.write_bytes(original)
            touched.append(p.name)
    return touched

# Human-readable hints injected into the prompt (kept out of the trusted oracle).
_HINTS = {
    "base64": ("base64.b64encode(x)",
               "standard Base64 (RFC 4648): map each 3 input bytes to 4 ASCII chars from "
               "'A-Za-z0-9+/'; pad the final group with '='. decode reverses this."),
    "base32": ("base64.b32encode(x)",
               "standard Base32 (RFC 4648 §6): alphabet 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567' "
               "(uppercase A-Z then digits 2-7); encode 5 input bytes (40 bits) as 8 chars; the "
               "final partial group is '=' padded (1/2/3/4 input bytes -> 6/4/3/1 trailing '='). "
               "decode reverses this; input is canonical uppercase with padding."),
    "bz2":  ("bz2.compress(x, 9)",
             "bzip2 (.bz2): run-length-encode, BWT, MTF, RLE2, then Huffman; "
             "stream header 'BZh' + level digit; per-block CRC."),
    "gzip": ("gzip.compress(x, compresslevel=9, mtime=0)",
             "gzip (RFC 1952) wrapping raw DEFLATE (RFC 1951): 10-byte header, "
             "DEFLATE body (LZ77 + Huffman, fixed/dynamic blocks), CRC32 + ISIZE trailer."),
    "zlib": ("zlib.compress(x, 9)",
             "zlib (RFC 1950): 2-byte header + raw DEFLATE (RFC 1951) + Adler-32 trailer."),
    "lzma": ("lzma.compress(x, preset=6)",
             "xz container (.xz) wrapping LZMA2: stream/block headers, range-coded "
             "LZMA with adaptive probabilities, CRC checks, index + footer."),
}


class Budget:
    """Per-codec wall-clock budget + the runaway kill switch."""

    def __init__(self, total_s: int = CODEC_BUDGET_S):
        self.total_s = total_s
        self._start = time.monotonic()

    def spent(self) -> float:
        return time.monotonic() - self._start

    def remaining(self) -> float:
        return max(0.0, self.total_s - self.spent())

    def exhausted(self) -> bool:
        return self.remaining() <= 0


def _build_prompt(codec: str, stage: str, last_fail: str | None) -> str:
    key = oracle.resolve(codec)
    module = oracle.forbidden_module(codec)
    settings, spec = _HINTS.get(key, (f"{module}.compress(x)", f"the {codec} format"))
    target = generated.decoder_path(codec)

    if stage == "m0":
        symbol, sig, verb = "decode", "def decode(data: bytes) -> bytes", "decompresses"
        what = (f"takes a complete {codec} compressed stream and returns the original "
                f"uncompressed bytes")
        law = (f"byte-exact: for varied inputs x, decode({settings.split('(')[0]}(x)) == x, "
               f"where the compressor is Python's stdlib `{module}` at {settings}")
    else:  # m2 — re-encoder
        symbol, sig, verb = "encode", "def encode(data: bytes) -> bytes", "re-encodes"
        what = (f"takes raw bytes x and returns a {codec} stream IDENTICAL to {settings}")
        law = f"byte-exact: encode(x) == {settings} for every x (match the reference bitstream)"

    prompt = f"""You are an autonomous coding agent. Implement a byte-exact, independent {codec} {symbol}r.

Write ONE Python 3 module to EXACTLY this absolute path:
    {target}

It must expose:
    {sig}
which {verb} {codec} data ({what}).

HARD REQUIREMENTS — validated automatically by a trusted oracle, no partial credit:
  1. {law}.
  2. INDEPENDENT: the module MUST NOT import `{module}` (or any submodule of it).
     The file is statically scanned and `{module}` is poisoned at runtime, so a
     {symbol}() that calls the real library will FAIL. Reimplement the format.
  3. Pure Python standard library only — no third-party packages, no network.
  4. Write ONLY the {symbol}r to that path. Do not leave a self-test that imports
     `{module}` in that file; test in a SEPARATE scratch script.

Format: {spec}
Style: reference decoders for other codecs live in {ORCH_DIR}/zstdct/
(gzip_decoder.py, lzma_decoder.py, bzip2_decoder.py) — match that engineering
rigor. Those files import their reference codec ONLY for self-tests; yours must
not import `{module}` at all.

Develop iteratively: in a scratch script you MAY use `{module}` to make sample
inputs and check your work. Cover empty input, short text, binary, long runs,
all 256 byte values, and periodic data. You are done when {symbol}() matches the
reference byte-for-byte across all of them. Then ensure the final file at the
path above contains the clean module with no `{module}` import."""

    if last_fail:
        prompt += f"""

PREVIOUS ATTEMPT FAILED the oracle. The file at {target} did not pass. Measured:
{last_fail}
Debug and rewrite the module so every case is byte-exact."""
    return prompt


def _run_agent(prompt: str, timeout_s: int) -> dict:
    """Invoke the selected generation agent once. Returns a status dict; never raises."""
    name, builder = selected_agent()
    if name is None or builder is None:
        return {"ok": False, "returncode": None, "stdout": "",
                "stderr": "no generation agent (hermes/codewhale) on PATH"}
    cmd = builder(prompt, GEN_AGENT_MODEL)
    try:
        proc = subprocess.run(
            cmd, cwd=str(REPO_ROOT),
            capture_output=True, text=True, timeout=timeout_s,
        )
        return {"ok": proc.returncode == 0, "returncode": proc.returncode,
                "stdout": proc.stdout[-4000:], "stderr": proc.stderr[-2000:]}
    except subprocess.TimeoutExpired:
        return {"ok": False, "returncode": None, "timeout": True,
                "stdout": "", "stderr": f"{name} exceeded {timeout_s}s"}
    except FileNotFoundError:
        return {"ok": False, "returncode": None,
                "stdout": "", "stderr": f"{name} binary not found"}


def _validate(codec: str, stage: str) -> dict:
    """Run the trusted oracle on whatever is currently at the generated path."""
    path = generated.decoder_path(codec)
    if not path.exists():
        return {"byte_exact": False, "error": "no file written", "fails": []}
    source = path.read_text()
    if stage == "m0":
        ok, reason = oracle.static_guard(source, codec)
        if not ok:
            return {"byte_exact": False, "error": reason, "fails": [reason]}
    try:
        mod = generated._load_module(path)
    except Exception as e:
        return {"byte_exact": False, "error": f"load: {type(e).__name__}: {e}", "fails": []}
    try:
        if stage == "m0":
            return oracle.validate_m0(codec, mod.decode)
        return oracle.validate_m2(codec, mod.encode)
    except AttributeError:
        sym = "decode" if stage == "m0" else "encode"
        return {"byte_exact": False, "error": f"module has no {sym}()", "fails": []}


def decompose(job, log=print) -> dict:
    """Generate + validate a decoder/encoder for one job, with retries + budget.

    Returns an outcome dict:
      {"generated": True,  "stage", "attempts", "validation"}            on success
      {"generated": False, "reason": "...", ...}                         otherwise
    The loop is responsible for turning this into a gated result (it registers the
    trusted verifier and calls run_stage); the decomposer only makes the artifact
    exist and pass the oracle, or fails honestly.
    """
    codec = job.get("codec", "unknown")
    stage = job.get("stage", "unknown")

    if stage not in GENERATABLE:
        return {"generated": False, "reason": "stage_not_generatable",
                "detail": f"{stage} has no hard byte-exact oracle; not auto-generated"}
    if not oracle.has_oracle(codec):
        return {"generated": False, "reason": "no_oracle",
                "detail": f"no reference codec for {codec}"}
    if not DRY_RUN and not agent_available():
        return {"generated": False, "reason": "agent_unavailable",
                "detail": "no generation agent (hermes/codewhale) on PATH"}

    generated.GEN_DIR.mkdir(parents=True, exist_ok=True)
    budget = Budget(CODEC_BUDGET_S)   # read module global at call time (test/env override)
    last_fail: str | None = None

    for attempt in range(1, MAX_RETRIES + 1):
        if budget.exhausted():
            return {"generated": False, "reason": "budget_exhausted",
                    "attempts": attempt - 1, "detail": f"codec budget {CODEC_BUDGET_S}s spent"}

        log(f"  [decompose] {codec}/{stage} attempt {attempt}/{MAX_RETRIES} "
            f"(budget {budget.remaining():.0f}s left)")
        prompt = _build_prompt(codec, stage, last_fail)
        timeout = int(min(ATTEMPT_TIMEOUT_S, budget.remaining()))

        if DRY_RUN:
            log("  [decompose] DRY_RUN: skipping agent call")
            run = {"ok": True, "returncode": 0, "stdout": "", "stderr": "", "dry_run": True}
        else:
            snap = _snapshot_trusted()
            run = _run_agent(prompt, timeout)
            touched = _restore_trusted(snap)
            if touched:
                log(f"  [decompose] SECURITY: {agent_name()} edited trusted file(s) {touched}; "
                    f"restored to pristine before validation")
        if not run["ok"] and run.get("timeout"):
            last_fail = str(run.get("stderr") or "agent timeout")
            log(f"  [decompose] {agent_name()} timed out ({timeout}s)")
            continue
        if not run["ok"] and run.get("returncode") not in (0, None):
            log(f"  [decompose] {agent_name()} exited {run.get('returncode')}: {run.get('stderr','')[:200]}")

        v = _validate(codec, stage)
        if v.get("byte_exact"):
            log(f"  [decompose] PASS oracle: {v.get('files_decoded', v.get('reencode_samples'))}"
                f"/{v.get('test_cases')} byte-exact on attempt {attempt}")
            return {"generated": True, "stage": stage, "codec": codec,
                    "attempts": attempt, "validation": v}

        last_fail = v.get("error") or "; ".join(v.get("fails", [])) or "not byte-exact"
        log(f"  [decompose] attempt {attempt} did not pass: {last_fail[:160]}")

    return {"generated": False, "reason": "max_retries", "attempts": MAX_RETRIES,
            "detail": last_fail or "no byte-exact decoder produced"}


if __name__ == "__main__":
    print("decomposer preflight")
    _name, _ = selected_agent()
    print(f"  generation agent: {_name or 'NONE'} ({shutil.which(_name) if _name else '-'})")
    print(f"  retries={MAX_RETRIES} attempt_timeout={ATTEMPT_TIMEOUT_S}s "
          f"codec_budget={CODEC_BUDGET_S}s dry_run={DRY_RUN}")
    # DRY_RUN smoke: exercise the control flow without calling the model.
    if DRY_RUN:
        out = decompose({"codec": "bz2", "stage": "m0"})
        print("  dry-run outcome:", out)
