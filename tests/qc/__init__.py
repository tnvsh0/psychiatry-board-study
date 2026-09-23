"""The QC test package.

Importing it forbids any real agy call for the rest of the process.

On 2026-09-23 the §4 known-answer probe was wired into qc_judge, and the suite
went from 35 seconds to 273: `deps.probe` resolves lazily to the REAL probe, so
a test that changed the agy version mid-run made an actual agy call — the
owner's quota, spent by a unit test, with no way to notice except the clock.
Tests own their doubles; this is the backstop for the ones that forget.
"""
import os

os.environ["QC_NO_AGY"] = "1"
