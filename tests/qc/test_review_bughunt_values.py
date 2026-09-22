#!/usr/bin/env python3
"""Adversarial bug hunt — the same dose, written two ways, is not "the same".

Offline. No books, no agy, no network.

THE DEFECT. `qc_norm.locked_values` canonicalizes a per-day dose only when the
"per" is a SLASH: `40 מ"ג/יום` and `40 mg/day` both come back as
`(40, 'mg/day')`. When the "per" is the preposition — `40 מ"ג ליום`,
`10 מ"ג ביום`, `2,400 mg ליום`, `40 mg per day` — the word after the unit is
never consulted and the value comes back as `(40, 'mg')`, in a different
`_bucket` from `(40, 'mg/day')`.

`values_dispute` compares only inside a bucket, so the two forms never meet:

    values_dispute('40 מ"ג ליום', 'the usual dose is 40 mg/day')
        -> {'disputed': False, 'same': False}

`same` is the drop rule. qc_judge._verify_contradicted reads exactly that
field (qc_judge.py:860) and drops the finding only when it is True, so a judge
that quotes a page stating the SAME dose as the material has its finding
VERIFIED, embedded by qc_fix Tier 1, and shown to the learner as
"נכתב כאן … → לפי הספר …" with identical numbers on both sides. §2.3 names a
false `contradicted` as the one outcome this mechanism exists to prevent, and
this is the only code that prevents it.

The phrasing is not hypothetical: `40 מ"ג ליום` and `60 מ"ג ליום` (029),
`15-30 מ"ג ליום` (042), `0.5-1 מ"ג ליום` (057) and `2,400 mg ליום` (046) are
all in the released chapters sampled under scratchpad/qc_samples.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import qc_norm                                             # noqa: E402

PER_DAY = [
    # (as the material writes it, as a textbook writes it)
    ('40 מ"ג ליום', "the usual dose is 40 mg/day"),          # 029
    ('60 מ"ג ליום', "up to 60 mg/day"),                      # 029
    ('10 מ"ג ביום', "10 mg/day"),
    ("2,400 mg ליום", "2,400 mg/day"),                       # 046
    ("40 mg per day", "40 mg/day"),
]


class APerDayDoseKeepsItsUnit(unittest.TestCase):
    def test_the_preposition_form_canonicalizes_like_the_slash_form(self):
        for said, _source in PER_DAY:
            with self.subTest(said=said):
                got = qc_norm.locked_values(said)
                self.assertTrue(got, f"{said!r} produced no locked value")
                kind, value = got[0]["kind"], got[0]["value"]
                self.assertEqual(
                    (kind, value[1]), ("dose", "mg/day"),
                    f"{said!r} parsed as {value!r}: the 'per day' was dropped, "
                    f"so it can never be compared with a book that writes "
                    f"mg/day")


class TheSameValueOnBothSidesIsDropped(unittest.TestCase):
    """§5.8: 'a finding whose quote holds the same value as `said` is dropped'."""

    def test_an_identical_dose_written_two_ways_is_recognised_as_the_same(self):
        for said, source in PER_DAY:
            with self.subTest(said=said):
                r = qc_norm.values_dispute(said, source)
                self.assertTrue(
                    r["same"],
                    f"values_dispute({said!r}, {source!r}) -> same={r['same']}: "
                    f"the page states the very dose the material states, and "
                    f"qc_judge drops a contradiction only on `same`, so this "
                    f"finding is verified and published as a correction")
                self.assertFalse(
                    r["disputed"],
                    f"{said!r} vs {source!r} was reported as a dispute")

    def test_a_real_disagreement_in_the_same_forms_is_disputed(self):
        """The other direction: a genuine conflict must still be a conflict."""
        r = qc_norm.values_dispute('40 מ"ג ליום', "the maximum is 60 mg/day")
        self.assertTrue(
            r["disputed"],
            "40 mg/day against 60 mg/day is not reported as disputed, so a "
            "real dose error carries no value_dispute into the verdict")


if __name__ == "__main__":
    unittest.main()
