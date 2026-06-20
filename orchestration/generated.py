#!/usr/bin/env python3
"""
generated.py — trusted glue between a machine-written decoder and the gate.

Codewhale writes `generated/<codec>_decoder.py` exposing `decode(data)->bytes`
(M0) and/or `encode(data)->bytes` (M2). This module provides the TRUSTED
verifier that the loop registers for (codec, stage): it loads that generated
module, runs it through the byte-exact oracle (oracle.py), and emits Evidence
built only from MEASURED results. The generated code never authors the metrics
the gate reads — that is the whole legitimacy guarantee, preserved even though a
machine wrote the decoder.

Stage coverage here is exactly the part with a HARD byte-exact oracle:
  M0 (decoder)  — decode(compress(x)) == x for the whole corpus
  M2 (encoder)  — encode(x) == reference.compress(x) for the whole corpus
M1/M3/M4 have no single hard oracle; they are handled honestly elsewhere
(decomposer reports `partial`, never a fabricated `pass`).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import oracle
from verify import Evidence, VERIFIERS, ensure_verifiers_loaded

ORCH_DIR = Path(__file__).resolve().parent
GEN_DIR = ORCH_DIR / "generated"

# Stages this module can hard-gate (have a byte-exact oracle).
HARD_GATED = ("m0", "m2")


def decoder_path(codec: str) -> Path:
    """Canonical path for a codec's generated module (aliases share one file)."""
    return GEN_DIR / f"{oracle.resolve(codec)}_decoder.py"


def _load_module(path: Path):
    """Import a generated module from its file path in a fresh namespace."""
    spec = importlib.util.spec_from_file_location(f"generated_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load generated module at {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _measured_lines(source: str) -> int:
    return len(source.splitlines())


def make_m0_verifier(codec: str):
    """Trusted M0 verifier: independent-decoder check + byte-exact oracle."""

    def verify(job) -> Evidence:
        path = decoder_path(codec)
        if not path.exists():
            return Evidence(
                checks={"decoder_present": False},
                metrics={"byte_exact": False, "files_decoded": 0, "test_cases": 0},
                scope="full",
                detail=f"no generated decoder at {path.name}",
                tags=["generated", "missing"],
            )
        source = path.read_text()

        # Anti-delegation: a generated decoder must not import the reference codec.
        independent, reason = oracle.static_guard(source, codec)
        if not independent:
            return Evidence(
                checks={"independent_decoder": False},
                metrics={"byte_exact": False, "files_decoded": 0,
                         "test_cases": len(oracle.corpus()),
                         "decoder_lines": _measured_lines(source)},
                scope="full",
                detail=f"generated decoder delegates to the reference codec: {reason}",
                tags=["generated", "delegation_rejected"],
            )

        try:
            mod = _load_module(path)
            decode = mod.decode
        except Exception as e:
            return Evidence(
                checks={"decoder_loads": False},
                metrics={"byte_exact": False, "files_decoded": 0,
                         "test_cases": len(oracle.corpus()),
                         "decoder_lines": _measured_lines(source)},
                scope="full",
                detail=f"generated decoder failed to load: {type(e).__name__}: {e}",
                tags=["generated", "load_error"],
            )

        m = oracle.validate_m0(codec, decode)
        detail = (f"{codec} M0: machine-written independent decoder vs real "
                  f"{oracle.resolve(codec)} — {m['files_decoded']}/{m['test_cases']} "
                  f"byte-exact (reference codec poisoned at runtime).")
        if m["fails"]:
            detail += " Failures: " + "; ".join(m["fails"][:3])

        return Evidence(
            checks={
                "all_byte_exact": m["byte_exact"],
                "corpus_min": m["test_cases"] >= oracle.MIN_FILES,
                "independent_decoder": True,
            },
            metrics={
                "byte_exact": m["byte_exact"],
                "files_decoded": m["files_decoded"],
                "test_cases": m["test_cases"],
                "decoder_lines": _measured_lines(source),
            },
            scope="full",
            detail=detail,
            tags=["generated", "decoder", "byte_exact"] + oracle.REFERENCE[oracle.resolve(codec)]["tags"],
        )

    return verify


def make_m2_verifier(codec: str):
    """Trusted M2 verifier: generated re-encoder reproduces real output byte-exact."""

    def verify(job) -> Evidence:
        path = decoder_path(codec)
        if not path.exists():
            return Evidence(
                checks={"encoder_present": False},
                metrics={"byte_exact": False, "reencode_samples": 0},
                scope="full",
                detail=f"no generated module at {path.name}",
                tags=["generated", "missing"],
            )
        source = path.read_text()
        try:
            mod = _load_module(path)
            encode = mod.encode
        except AttributeError:
            return Evidence(
                checks={"encoder_present": False},
                metrics={"byte_exact": False, "reencode_samples": 0,
                         "decoder_lines": _measured_lines(source)},
                scope="full",
                detail="generated module has no encode() for M2",
                tags=["generated", "missing_encoder"],
            )
        except Exception as e:
            return Evidence(
                checks={"encoder_loads": False},
                metrics={"byte_exact": False, "reencode_samples": 0},
                scope="full",
                detail=f"generated module failed to load: {type(e).__name__}: {e}",
                tags=["generated", "load_error"],
            )

        m = oracle.validate_m2(codec, encode)
        detail = (f"{codec} M2 (Direction 1): machine-written re-encoder reproduces real "
                  f"{oracle.resolve(codec)} output on {m['reencode_samples']}/{m['test_cases']} "
                  f"corpus files byte-for-byte.")
        if m["fails"]:
            detail += " Failures: " + "; ".join(m["fails"][:3])

        return Evidence(
            checks={"all_byte_exact": m["byte_exact"], "corpus_min": m["test_cases"] >= 1},
            metrics={
                "byte_exact": m["byte_exact"],
                "reencode_samples": m["reencode_samples"],
                "test_cases": m["test_cases"],
            },
            scope="full",
            detail=detail,
            tags=["generated", "reencode", "direction1", "byte_exact"],
        )

    return verify


_FACTORY = {"m0": make_m0_verifier, "m2": make_m2_verifier}


def register_generated(codec: str, stage: str) -> bool:
    """Wire a trusted generated-decoder verifier into verify.VERIFIERS for
    (codec, stage). Returns True if a verifier was registered (stage is hard-
    gated), False otherwise. Keyed on the job's raw codec string so the loop's
    run_stage(job) finds it; the oracle is resolved (alias-aware) internally."""
    factory = _FACTORY.get(stage)
    if factory is None:
        return False
    # Load built-ins FIRST so the generated verifier overrides them, rather than
    # being silently clobbered when run_stage later lazy-loads the built-ins.
    ensure_verifiers_loaded()
    VERIFIERS[(codec, stage)] = factory(codec)
    return True


def has_generated_decoder(codec: str) -> bool:
    try:
        return decoder_path(codec).exists()
    except oracle.OracleError:
        return False
