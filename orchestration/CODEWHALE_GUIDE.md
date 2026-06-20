# Codewhale Stage Guide

What to do at each M0-M4 stage. Codewhale (deepseek) executes these, submits JSON results.

## Workflow

```
1. Call: python codewhale_interface.py --get-job
   ↓ Returns JSON with codec, stage, spec_url, notes
2. Do the work for that stage
3. Write results to JSON file
4. Call: python codewhale_interface.py --submit-job <job_id> --result-file <path>
```

## Stages

### M0: Byte-Exact Decoder

**Goal**: Build a gated decoder that validates you understand the bitstream.

**Deliverable**: 
- Python class `{Codec}Decoder` that reads the compressed format byte-by-byte
- Test suite validating 100+ real files (download from public sources)
- Coverage: all fields in the RFC/spec

**Checklist**:
- [ ] Decoder parses all RFC sections
- [ ] Test suite passes (100+ files)
- [ ] Byte-for-byte match on decompressed output
- [ ] Handle all block types / frame formats
- [ ] No external libraries (except the standard compressor if available)

**Result JSON**:
```json
{
  "job_id": "codec_m0",
  "codec": "codec",
  "stage": "m0",
  "status": "pass",
  "metrics": {
    "decoder_lines": <lines of code>,
    "test_cases": <number of test files>,
    "coverage": <fraction 0-1>
  },
  "notes": "RFC compliance notes"
}
```

---

### M1: Kill-Switch (Preimage-Mirrors-Mode)

**Goal**: Validate the hypothesis that **preimage mirrors the algorithm**.

**Approach**:
- Encode → get bitstream → decode (separate process)
- Compare: does the structure of the bitstream match what you expect from the algorithm?
- Specifically: can you read off *how* the encoder made choices just by parsing the output?

**Deliverable**:
- Validation suite showing preimage-mirrors-mode holds
- Evidence: reusing the structure from M0 decoder, you can reconstruct *why* each bit is there
- Pass/fail: boolean verdict

**Result JSON**:
```json
{
  "job_id": "codec_m1",
  "codec": "codec",
  "stage": "m1",
  "status": "pass",
  "metrics": {
    "preimage_mirrors_mode": true,
    "test_cases": 100
  },
  "notes": "Algorithm structure is visible in bitstream"
}
```

If preimage-mirrors-mode fails, mark status="fail" and stop here (don't queue M2-M4).

---

### M2: Direction 1 — Reencode (Reproduce Output)

**Goal**: Build a streaming encoder that reproduces the real encoder's output byte-for-byte.

**Deliverable**:
- Streaming encoder class
- For 100+ test files: compress → decompress → verify matches original
- Must reproduce real compressor output exactly (including frame headers, block boundaries)

**Approach**:
- Use the structure from M1 (preimage mirrors mode)
- Implement the encoding path by inverting the decoder
- Test against real files compressed by the official compressor

**Result JSON**:
```json
{
  "job_id": "codec_m2",
  "codec": "codec",
  "stage": "m2",
  "status": "pass",
  "metrics": {
    "byte_exact": true,
    "reencode_samples": 200,
    "coverage": 0.95
  },
  "notes": "Reencode matches zlib/official compressor byte-for-byte"
}
```

---

### M3: Direction 2 — Preimage Analysis (Find the Cell/Orbit)

**Goal**: Locate the structural thinning mechanism (Mode A/B split).

**Pattern to find**:
- **Mode A (symmetry or counting)**: Structure where redundancy is removed by selecting from equivalence classes OR by transmitting a model
  - **Counting cell**: explicit model on wire (frequency table, weights, alphabet)
  - **Symmetry orbit**: implicit model, reused structure (e.g., BWT's origPtr feedback)
- **Mode B (bijection)**: structure where *only one choice* is available at each step (prices, not thins)

**Deliverable**:
- Identify which Mode A variant applies (counting vs orbit vs both)
- Locate specific cells/orbits in the bitstream
- Count occurrences
- Compare to gauge size (the degree of freedom you have)

**Result JSON**:
```json
{
  "job_id": "codec_m3",
  "codec": "codec",
  "stage": "m3",
  "status": "pass",
  "metrics": {
    "cell_count": 42,
    "orbit_count": 0,
    "mode_a_flavor": "counting"
  },
  "tags": ["static_model", "huffman", "counting_cell"],
  "notes": "Counting cell on Huffman alphabet (static model, transmitted)"
}
```

---

### M4: Synthesis (Integrate + Compare)

**Goal**: Summarize findings and place this codec in the taxonomy.

**Deliverable**:
- Unified description: does preimage-mirrors-mode hold? which Mode A/B structure?
- Compare to known codecs (bzip2, zstd, LZMA, gzip) — where does this fit?
- Highlight novel structures (if any)

**Result JSON**:
```json
{
  "job_id": "codec_m4",
  "codec": "codec",
  "stage": "m4",
  "status": "pass",
  "tags": ["static_model", "deflate_variant", "counting_cell"],
  "comparison": "DEFLATE variant with recursive Kraft; counting cell vs gzip's degenerate cell",
  "notes": "Preimage-mirrors-mode: YES. Mode A: counting (static model, weights on wire). Mode B: bijection (prices). Distinctive: recursive Kraft nesting (outer + inner)."
}
```

---

## Key Files to Reuse

- `src/zstdct/spoonfeed.py` — the methodology (coarsening_cell, gauge, kraft_sum, etc.)
- `findings/` — reference results from known codecs (bzip2, zstd, LZMA, gzip)
- `src/zstdct/{decoder,encoder}_*.py` — example decoders/encoders to learn from

## Resources

- **Brotli (RFC7932)**: https://tools.ietf.org/html/rfc7932
- **LZ4**: https://github.com/lz4/lz4/blob/dev/doc/lz4_Frame_format.md
- **Snappy**: https://github.com/google/snappy/blob/master/format_description.txt
- **Zopfli**: https://en.wikipedia.org/wiki/Deflate

## Submission

```bash
python orchestration/codewhale_interface.py --submit-job <job_id> --result-file /path/to/result.json
```

If you get stuck on a stage, mark status="fail" and include an error message. Hermes will log it and you can pick up the next codec.
