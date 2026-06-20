"""Vendored subset of the ZSTDCT spoon-feed analysis code, used by the M1-M4
codec verifiers. Copied verbatim from src/zstdct so the device can run the same
proven decode/re-encode/structure analysis. Requires numpy (present on the device)."""
