#!/usr/bin/env python3
"""
Tests for the decomposer-inside-the-loop: oracle, trusted glue, generate flow.

The legitimacy claim under test: a machine writes the decoder, but ONLY a real
byte-exact match against the reference codec can mint a `pass`, and a decoder
that delegates to the reference codec is rejected. No DeepSeek/Codewhale call is
made here — generation runs in DRY_RUN and the "generated" artifact is a known
independent decoder, so the whole gate path is exercised offline.
"""
import pytest

import oracle
import generated
import decomposer
import verify

# An independent decoder, re-exported. Its OWN source imports `zstdct`, not `bz2`,
# so it passes the anti-delegation guard; it decodes without the reference codec.
HONEST_BZ2 = "from zstdct.bzip2_decoder import decode_bz2 as decode\n"
DELEGATING_BZ2 = "import bz2\ndef decode(data):\n    return bz2.decompress(data)\n"
WRONG_BZ2 = "def decode(data):\n    return b'wrong'\n"


@pytest.fixture
def gen_dir(tmp_path, monkeypatch):
    """Point the generated-decoder dir at a tmp path and clear stale registry."""
    monkeypatch.setattr(generated, "GEN_DIR", tmp_path)
    verify.VERIFIERS.pop(("bz2", "m0"), None)
    yield tmp_path
    verify.VERIFIERS.pop(("bz2", "m0"), None)


# --- oracle ---------------------------------------------------------------

def test_reference_codecs_roundtrip():
    for name in oracle.REFERENCE:
        comp = oracle.REFERENCE[name]["compress"]
        deco = oracle.REFERENCE[name]["decompress"]
        assert all(deco(comp(x)) == x for x in oracle.corpus())


def test_aliases_resolve():
    assert oracle.resolve("bzip2") == "bz2"
    assert oracle.resolve("xz") == "lzma"
    with pytest.raises(oracle.OracleError):
        oracle.resolve("not_a_codec")


def test_static_guard_rejects_delegation_allows_independent():
    ok, reason = oracle.static_guard(DELEGATING_BZ2, "bz2")
    assert not ok and "bz2" in reason
    ok, _ = oracle.static_guard(HONEST_BZ2, "bz2")
    assert ok


def test_validate_m0_passes_for_independent_decoder():
    from zstdct.bzip2_decoder import decode_bz2
    m = oracle.validate_m0("bz2", decode_bz2)
    assert m["byte_exact"] and m["files_decoded"] == m["test_cases"] >= oracle.MIN_FILES


def test_validate_m0_poison_blocks_delegation():
    # A decoder that calls the real bz2.decompress must NOT validate.
    def cheat(data):
        import bz2
        return bz2.decompress(data)
    m = oracle.validate_m0("bz2", cheat)
    assert not m["byte_exact"] and m["files_decoded"] == 0


def test_validate_m2_byte_exact_for_real_encoder():
    # The reference compressor itself is, trivially, a byte-exact re-encoder.
    enc = oracle.REFERENCE["bz2"]["compress"]
    m = oracle.validate_m2("bz2", enc)
    assert m["byte_exact"] and m["reencode_samples"] == m["test_cases"]


# --- trusted glue through the gate ---------------------------------------

def _run(codec, stage):
    return verify.run_stage({"id": f"{codec}_{stage}", "codec": codec, "stage": stage})


def test_generated_honest_decoder_passes_gate(gen_dir):
    (gen_dir / "bz2_decoder.py").write_text(HONEST_BZ2)
    assert generated.register_generated("bz2", "m0")
    res = _run("bz2", "m0")
    ok, errs = verify.validate_result(res)
    assert res["status"] == "pass" and ok, (res.get("status"), errs)
    assert res["metrics"]["byte_exact"] is True


def test_generated_delegation_fails_gate(gen_dir):
    (gen_dir / "bz2_decoder.py").write_text(DELEGATING_BZ2)
    generated.register_generated("bz2", "m0")
    res = _run("bz2", "m0")
    assert res["status"] == "fail"
    assert verify.validate_result(res)[0] is True   # an honest fail still passes the gate's shape check


def test_generated_wrong_output_fails_gate(gen_dir):
    (gen_dir / "bz2_decoder.py").write_text(WRONG_BZ2)
    generated.register_generated("bz2", "m0")
    res = _run("bz2", "m0")
    assert res["status"] == "fail"


def test_missing_decoder_is_not_a_pass(gen_dir):
    generated.register_generated("bz2", "m0")          # registered, but no file present
    res = _run("bz2", "m0")
    assert res["status"] != "pass"


# --- decomposer control flow (DRY_RUN; no model call) --------------------

@pytest.fixture
def dry(monkeypatch):
    monkeypatch.setattr(decomposer, "DRY_RUN", True)
    monkeypatch.setattr(decomposer, "MAX_RETRIES", 2)


def test_decompose_success_when_decoder_present(gen_dir, dry):
    (gen_dir / "bz2_decoder.py").write_text(HONEST_BZ2)
    out = decomposer.decompose({"codec": "bz2", "stage": "m0"}, log=lambda *_: None)
    assert out["generated"] and out["validation"]["byte_exact"]


def test_decompose_gives_up_honestly(gen_dir, dry):
    out = decomposer.decompose({"codec": "bz2", "stage": "m0"}, log=lambda *_: None)
    assert not out["generated"] and out["reason"] == "max_retries"


def test_decompose_skips_non_generatable_stage(dry):
    out = decomposer.decompose({"codec": "bz2", "stage": "m1"}, log=lambda *_: None)
    assert not out["generated"] and out["reason"] == "stage_not_generatable"


def test_decompose_skips_codec_without_oracle(dry):
    out = decomposer.decompose({"codec": "frobnicate", "stage": "m0"}, log=lambda *_: None)
    assert not out["generated"] and out["reason"] == "no_oracle"


def test_budget_runaway_kill_switch(gen_dir, monkeypatch):
    monkeypatch.setattr(decomposer, "DRY_RUN", True)
    monkeypatch.setattr(decomposer, "CODEC_BUDGET_S", 0)   # already exhausted
    out = decomposer.decompose({"codec": "bz2", "stage": "m0"}, log=lambda *_: None)
    assert not out["generated"] and out["reason"] == "budget_exhausted"


# --- loop integration -----------------------------------------------------

def test_trusted_file_restore_guard(tmp_path, monkeypatch):
    f = tmp_path / "oracle.py"
    f.write_bytes(b"PRISTINE TRUSTED CODE")
    monkeypatch.setattr(decomposer, "TRUSTED_FILES", [f])
    snap = decomposer._snapshot_trusted()
    f.write_bytes(b"WEAKENED BY THE AGENT")           # simulate tampering
    touched = decomposer._restore_trusted(snap)
    assert touched == ["oracle.py"]
    assert f.read_bytes() == b"PRISTINE TRUSTED CODE"  # self-healed


def test_trusted_file_guard_no_false_positive(tmp_path, monkeypatch):
    f = tmp_path / "verify.py"
    f.write_bytes(b"X")
    monkeypatch.setattr(decomposer, "TRUSTED_FILES", [f])
    snap = decomposer._snapshot_trusted()
    assert decomposer._restore_trusted(snap) == []     # untouched -> nothing to restore


def test_loop_reports_honestly_without_agent(gen_dir, monkeypatch):
    import codewhale_autonomous as cw
    monkeypatch.setattr(decomposer, "DRY_RUN", False)
    monkeypatch.setattr(decomposer, "agent_available", lambda: False)
    monkeypatch.setattr(cw, "agent_available", lambda: False)
    res = cw.work_on_job({"id": "bz2_m0", "codec": "bz2", "stage": "m0", "spec_url": "-"})
    assert res["status"] == "not_implemented"
    assert verify.validate_result(res)[0] is True


def test_agent_autodetect_and_pin(monkeypatch):
    # hermes preferred when both present; GEN_AGENT pins; argv shape differs per agent.
    monkeypatch.setattr(decomposer.shutil, "which", lambda b: f"/usr/bin/{b}")
    monkeypatch.delenv("GEN_AGENT", raising=False)
    assert decomposer.selected_agent()[0] == "hermes"
    monkeypatch.setenv("GEN_AGENT", "codewhale")
    name, builder = decomposer.selected_agent()
    assert name == "codewhale"
    assert builder("PROMPT", "")[:3] == ["codewhale", "exec", "--auto"]
    assert decomposer._hermes_argv("PROMPT", "")[:2] == ["hermes", "-z"]
