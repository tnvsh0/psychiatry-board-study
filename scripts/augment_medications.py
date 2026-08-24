#!/usr/bin/env python3
"""
Augment schedule/medications.json — fill gaps for full general-psychiatry
coverage (per user 2026-05-10: need ALL meds, not only pediatric-approved;
geriatric/dementia-specific drugs are EXCLUDED).

Categories of additions:
1. Additional antidepressants (TCAs, MAOIs, SNRIs, novel)
2. Additional antipsychotics (lumateperone, loxapine, classic 1st-gen,
   pimavanserin, long-acting injectable concept)
3. Specific benzodiazepines (split out by t½ profile)
4. Anticonvulsant in psychiatry: topiramate
5. Addiction treatment: methadone, disulfiram, varenicline, naloxone,
   nicotine replacement
6. PTSD/anxiety adjuncts: prazosin, propranolol
7. Tardive dyskinesia treatments: valbenazine, deutetrabenazine,
   tetrabenazine
8. Anticholinergics for EPS: benztropine, trihexyphenidyl, diphenhydramine
9. Postpartum: zuranolone (oral neuroactive steroid)
10. Newer sleep aids: lemborexant
11. Other: cyproheptadine (5-HT2 antagonist — appetite + serotonin
    syndrome reversal)
12. Anesthetic-rapid antidepressants: ketamine (IV), distinct from esketamine

EXCLUDED (per user — geriatric only):
- Donepezil, rivastigmine, galantamine (cholinesterase inhibitors)
- Memantine (NMDA antagonist for AD)
"""
import json
from pathlib import Path

import sys
# Windows gives a redirected stdout the locale codec (cp1255 here), and a
# single Hebrew character then raises UnicodeEncodeError. Every chapter
# title in this course is Hebrew, and bulk_run redirects stdout to a log.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
MED_FILE = ROOT / "schedule" / "medications.json"

ADDITIONS = [
    # ── Antidepressants additions ────────────────────────────────────────────
    {"generic_name": "Desvenlafaxine", "drug_class": "Antidepressant — SNRI",
     "brand_examples": ["Pristiq"], "key_indications_psych": ["MDD"],
     "primary_receptors_actions": ["SERT inhibitor", "NET inhibitor"],
     "_notes": "Active metabolite of venlafaxine. Less hepatic metabolism — fewer DDIs."},
    {"generic_name": "Levomilnacipran", "drug_class": "Antidepressant — SNRI",
     "brand_examples": ["Fetzima"], "key_indications_psych": ["MDD"],
     "primary_receptors_actions": ["NET > SERT inhibitor"],
     "_notes": "More NE-selective than other SNRIs."},
    {"generic_name": "Nortriptyline", "drug_class": "Antidepressant — TCA (secondary amine)",
     "brand_examples": ["Pamelor"], "key_indications_psych": ["MDD","Neuropathic pain"],
     "primary_receptors_actions": ["NET > SERT","Less anticholinergic than tertiary TCAs"],
     "_notes": "Best-tolerated TCA. Therapeutic window 50-150 ng/ml — narrow."},
    {"generic_name": "Amitriptyline", "drug_class": "Antidepressant — TCA (tertiary amine)",
     "brand_examples": ["Elavil"], "key_indications_psych": ["MDD","Migraine prophylaxis","Chronic pain","Insomnia"],
     "primary_receptors_actions": ["SERT","NET","H1","M1","α1"],
     "_notes": "Strong sedation + anticholinergic. Cardiotoxic in OD."},
    {"generic_name": "Doxepin", "drug_class": "Antidepressant — TCA (tertiary)",
     "brand_examples": ["Silenor"], "key_indications_psych": ["MDD","Insomnia (low-dose)","Pruritus"],
     "primary_receptors_actions": ["H1 antagonist (very potent at low dose)"],
     "_notes": "Low-dose (3-6mg) is pure H1 → FDA-approved for sleep."},
    {"generic_name": "Desipramine", "drug_class": "Antidepressant — TCA (secondary)",
     "brand_examples": ["Norpramin"], "key_indications_psych": ["MDD","ADHD (off-label adults)"],
     "primary_receptors_actions": ["NET selective"],
     "_notes": "Most NE-selective TCA. Activating."},
    {"generic_name": "Selegiline (transdermal)", "drug_class": "Antidepressant — MAOI (transdermal)",
     "brand_examples": ["Emsam"], "key_indications_psych": ["MDD"],
     "primary_receptors_actions": ["MAO-B selective at low dose; MAO-A&B at higher"],
     "_notes": "Patch — bypasses gut MAO so 6mg patch = no tyramine restriction. Higher doses need diet."},
    {"generic_name": "Isocarboxazid", "drug_class": "Antidepressant — MAOI",
     "brand_examples": ["Marplan"], "key_indications_psych": ["Treatment-resistant MDD"],
     "primary_receptors_actions": ["Irreversible MAO-A & MAO-B inhibitor"],
     "_notes": "Tyramine restriction. Rarely used."},
    {"generic_name": "Nefazodone", "drug_class": "Antidepressant — Atypical (SARI variant)",
     "brand_examples": ["Serzone (withdrawn US)"], "key_indications_psych": ["MDD"],
     "primary_receptors_actions": ["5-HT2A antagonist","weak SERT"],
     "_notes": "Black box: hepatotoxicity — rarely used now."},
    {"generic_name": "Zuranolone", "drug_class": "Antidepressant — Neuroactive steroid (oral)",
     "brand_examples": ["Zurzuvae"], "key_indications_psych": ["Postpartum depression"],
     "approved_for_pediatric": False,
     "primary_receptors_actions": ["GABA-A positive allosteric modulator"],
     "_notes": "Oral 14-day course. Approved 2023 for PPD — alternative to brexanolone IV."},
    {"generic_name": "Ketamine (IV/IM)", "drug_class": "Rapid-acting antidepressant — Anesthetic",
     "brand_examples": ["Ketalar (anesthesia branding)"], "key_indications_psych": ["Treatment-resistant MDD","Acute suicidality"],
     "primary_receptors_actions": ["NMDA receptor antagonist","Increases BDNF/synaptogenesis"],
     "_notes": "Off-label for MDD (esketamine = on-label nasal). Rapid onset (hours). Dissociation, BP↑."},

    # ── Antipsychotics additions ─────────────────────────────────────────────
    {"generic_name": "Lumateperone", "drug_class": "Antipsychotic — Second-generation",
     "brand_examples": ["Caplyta"], "key_indications_psych": ["Schizophrenia","Bipolar depression"],
     "primary_receptors_actions": ["5-HT2A antagonist","D2 partial (presynaptic) / mod. (postsynaptic)","SERT modulation"],
     "_notes": "Newer (2019). Low metabolic burden. No required titration."},
    {"generic_name": "Iloperidone", "drug_class": "Antipsychotic — Second-generation",
     "brand_examples": ["Fanapt"], "key_indications_psych": ["Schizophrenia","Bipolar I (mania) — newer indication"],
     "primary_receptors_actions": ["D2","5-HT2A","α1"],
     "_notes": "Slow titration needed (orthostasis from α1 block)."},
    {"generic_name": "Loxapine", "drug_class": "Antipsychotic — First-generation (mid-potency)",
     "brand_examples": ["Loxitane","Adasuve (inhaled)"],
     "key_indications_psych": ["Schizophrenia","Acute agitation (inhaled)"],
     "primary_receptors_actions": ["D2","5-HT2A (also has serotonin activity)"],
     "_notes": "Inhaled form for rapid IM-alternative agitation control."},
    {"generic_name": "Perphenazine", "drug_class": "Antipsychotic — First-generation (mid-potency)",
     "brand_examples": ["Trilafon"], "key_indications_psych": ["Schizophrenia"],
     "primary_receptors_actions": ["D2"],
     "_notes": "Mid-potency 1st-gen. Was the comparator in CATIE trial."},
    {"generic_name": "Trifluoperazine", "drug_class": "Antipsychotic — First-generation (high-potency)",
     "brand_examples": ["Stelazine"], "key_indications_psych": ["Schizophrenia","Anxiety (low-dose, off-label)"],
     "primary_receptors_actions": ["D2"],
     "_notes": "High-potency phenothiazine. EPS-prone."},
    {"generic_name": "Thiothixene", "drug_class": "Antipsychotic — First-generation (high-potency)",
     "brand_examples": ["Navane"], "key_indications_psych": ["Schizophrenia"],
     "primary_receptors_actions": ["D2"],
     "_notes": "Thioxanthene class."},
    {"generic_name": "Pimavanserin", "drug_class": "Antipsychotic — 5-HT2A inverse agonist",
     "brand_examples": ["Nuplazid"], "key_indications_psych": ["Parkinson's disease psychosis"],
     "approved_for_pediatric": False,
     "primary_receptors_actions": ["5-HT2A inverse agonist (no D2 activity)"],
     "_notes": "Unique — no D2 block → no EPS. Off-label in dementia psychosis (FDA box warning)."},
    {"generic_name": "Long-Acting Injectable Antipsychotics (concept)",
     "drug_class": "Antipsychotic — Long-acting injectable formulations",
     "brand_examples": ["Haldol decanoate","Risperdal Consta","Invega Sustenna/Trinza/Hafyera",
                        "Abilify Maintena/Aristada","Zyprexa Relprevv","Perseris"],
     "key_indications_psych": ["Schizophrenia (esp. non-adherent)","Schizoaffective"],
     "primary_receptors_actions": ["Same as parent oral compound"],
     "_notes": "Adherence solution. Half-lives weeks-months. Olanzapine LAI requires PDSS monitoring (post-injection delirium/sedation)."},

    # ── Specific Benzodiazepines (split out the lumped entry) ────────────────
    {"generic_name": "Alprazolam", "drug_class": "Benzodiazepine — short-acting, high-potency",
     "brand_examples": ["Xanax"], "key_indications_psych": ["Panic disorder","GAD"],
     "primary_receptors_actions": ["GABA-A positive modulator (α1/α2/α3/α5)"],
     "_notes": "High dependence potential. Inter-dose rebound. Avoid in adolescents."},
    {"generic_name": "Lorazepam", "drug_class": "Benzodiazepine — intermediate, no active metabolite",
     "brand_examples": ["Ativan"], "key_indications_psych": ["Anxiety","Status epilepticus","Catatonia (Lorazepam challenge)","Acute agitation"],
     "primary_receptors_actions": ["GABA-A positive modulator"],
     "_notes": "Hepatic conjugation only — safer in liver disease + elderly. IV/IM/PO."},
    {"generic_name": "Clonazepam", "drug_class": "Benzodiazepine — long-acting, high-potency",
     "brand_examples": ["Klonopin"], "key_indications_psych": ["Panic","Seizure (some)","Akathisia adjunct"],
     "primary_receptors_actions": ["GABA-A positive modulator"],
     "_notes": "Long t½ → less rebound than alprazolam. Often used long-term despite cautions."},
    {"generic_name": "Diazepam", "drug_class": "Benzodiazepine — long-acting, active metabolites",
     "brand_examples": ["Valium"], "key_indications_psych": ["Alcohol withdrawal","Anxiety","Muscle spasm","Seizure"],
     "primary_receptors_actions": ["GABA-A positive modulator"],
     "_notes": "Long active metabolites (desmethyldiazepam). Used in alcohol withdrawal."},
    {"generic_name": "Midazolam", "drug_class": "Benzodiazepine — ultra-short, parenteral",
     "brand_examples": ["Versed"], "key_indications_psych": ["Acute agitation (IM/IV)","Procedural sedation","Status epilepticus (IM/IN)"],
     "primary_receptors_actions": ["GABA-A positive modulator"],
     "_notes": "Rapid IM onset → emergency agitation. Water-soluble. Intranasal in pediatrics for seizure."},
    {"generic_name": "Oxazepam", "drug_class": "Benzodiazepine — short, no active metabolite",
     "brand_examples": ["Serax"], "key_indications_psych": ["Alcohol withdrawal (elderly/liver)","Anxiety"],
     "primary_receptors_actions": ["GABA-A positive modulator"],
     "_notes": "Like lorazepam — conjugation only, safe in liver disease."},
    {"generic_name": "Chlordiazepoxide", "drug_class": "Benzodiazepine — long-acting",
     "brand_examples": ["Librium"], "key_indications_psych": ["Alcohol withdrawal (CIWA protocol)"],
     "primary_receptors_actions": ["GABA-A positive modulator"],
     "_notes": "Classic alcohol-withdrawal taper agent (long t½)."},
    {"generic_name": "Temazepam", "drug_class": "Benzodiazepine — intermediate, hypnotic",
     "brand_examples": ["Restoril"], "key_indications_psych": ["Insomnia"],
     "primary_receptors_actions": ["GABA-A positive modulator"],
     "_notes": "Bedtime use. Conjugation metabolism."},

    # ── Mood stabilizer / Anticonvulsant ─────────────────────────────────────
    {"generic_name": "Topiramate", "drug_class": "Mood stabilizer / Anticonvulsant",
     "brand_examples": ["Topamax"], "key_indications_psych": ["Binge eating disorder","Alcohol use disorder","Migraine prophylaxis","BPD adjunct (off-label)"],
     "primary_receptors_actions": ["Multiple — Na+ block, GABA enhancer, glutamate antagonist"],
     "_notes": "Cognitive slowing (\"Dopamax\"). Weight loss. Kidney stones."},

    # ── Addiction treatment additions ────────────────────────────────────────
    {"generic_name": "Methadone", "drug_class": "Addiction treatment / Opioid full agonist",
     "brand_examples": ["Methadose","Dolophine"], "key_indications_psych": ["Opioid use disorder (maintenance)","Chronic pain"],
     "primary_receptors_actions": ["μ-opioid full agonist","NMDA antagonist","SERT/NET inhibitor"],
     "_notes": "Long t½ (24-36h). QTc prolongation. Methadone clinics (federal regulation)."},
    {"generic_name": "Disulfiram", "drug_class": "Addiction treatment — Alcohol aversion",
     "brand_examples": ["Antabuse"], "key_indications_psych": ["Alcohol use disorder (adherence-based)"],
     "primary_receptors_actions": ["Aldehyde dehydrogenase inhibitor → acetaldehyde build-up on EtOH"],
     "_notes": "Aversion therapy. CYP-2E1/CYP3A4 inhibitor → DDIs. Hepatotoxic."},
    {"generic_name": "Varenicline", "drug_class": "Addiction treatment — Nicotine cessation",
     "brand_examples": ["Chantix"], "key_indications_psych": ["Tobacco use disorder"],
     "primary_receptors_actions": ["α4β2 nAChR partial agonist"],
     "_notes": "Reduces craving + blocks nicotine reward. Neuropsych warnings (now removed)."},
    {"generic_name": "Naloxone", "drug_class": "Addiction treatment — Opioid antagonist (overdose reversal)",
     "brand_examples": ["Narcan","Kloxxado"], "key_indications_psych": ["Opioid overdose"],
     "primary_receptors_actions": ["μ-opioid antagonist"],
     "_notes": "Intranasal/IM/IV. Short t½ — may need re-dose. Precipitates withdrawal."},
    {"generic_name": "Nicotine Replacement Therapy",
     "drug_class": "Addiction treatment — Nicotine replacement",
     "brand_examples": ["Patch","Gum","Lozenge","Inhaler","Nasal spray"],
     "key_indications_psych": ["Tobacco use disorder"],
     "primary_receptors_actions": ["nAChR agonist (slow, sustained)"],
     "_notes": "First-line. Combine long-acting (patch) + short-acting (gum) for craving."},

    # ── Anticholinergics for EPS ─────────────────────────────────────────────
    {"generic_name": "Benztropine", "drug_class": "Anticholinergic — EPS treatment",
     "brand_examples": ["Cogentin"], "key_indications_psych": ["Acute dystonia","Parkinsonism"],
     "primary_receptors_actions": ["Muscarinic antagonist","mild antihistamine"],
     "_notes": "First-line for acute dystonia (IM 1-2mg). Long t½."},
    {"generic_name": "Trihexyphenidyl", "drug_class": "Anticholinergic — EPS treatment",
     "brand_examples": ["Artane"], "key_indications_psych": ["Drug-induced parkinsonism","Dystonia"],
     "primary_receptors_actions": ["Muscarinic antagonist"],
     "_notes": "Oral. Less sedating than benztropine."},
    {"generic_name": "Diphenhydramine", "drug_class": "Anticholinergic / Antihistamine — EPS",
     "brand_examples": ["Benadryl"], "key_indications_psych": ["Acute dystonia (IM alternative to benztropine)","Insomnia (OTC)"],
     "primary_receptors_actions": ["H1 antagonist","muscarinic antagonist"],
     "_notes": "IM 25-50mg for dystonia. Sedating."},

    # ── PTSD / anxiety adjuncts ──────────────────────────────────────────────
    {"generic_name": "Prazosin", "drug_class": "α1-adrenergic antagonist",
     "brand_examples": ["Minipress"], "key_indications_psych": ["PTSD nightmares","BPH"],
     "primary_receptors_actions": ["α1 antagonist (peripheral + central)"],
     "_notes": "Off-label for PTSD nightmares. Bedtime dosing. First-dose orthostasis."},
    {"generic_name": "Propranolol", "drug_class": "Non-selective β-blocker",
     "brand_examples": ["Inderal"], "key_indications_psych": ["Performance anxiety","Akathisia","Aggression (TBI/ID)","PTSD prevention (debated)"],
     "primary_receptors_actions": ["β1 + β2 antagonist"],
     "_notes": "Lipophilic — crosses BBB. Caution: bronchospasm, depression."},

    # ── Tardive Dyskinesia treatments ────────────────────────────────────────
    {"generic_name": "Valbenazine", "drug_class": "VMAT2 inhibitor — Tardive Dyskinesia",
     "brand_examples": ["Ingrezza"], "key_indications_psych": ["Tardive dyskinesia"],
     "primary_receptors_actions": ["VMAT2 inhibitor → ↓dopamine release"],
     "_notes": "Newer (2017). Once-daily. Approved for TD."},
    {"generic_name": "Deutetrabenazine", "drug_class": "VMAT2 inhibitor — Tardive Dyskinesia / Huntington's",
     "brand_examples": ["Austedo"], "key_indications_psych": ["Tardive dyskinesia","Huntington's chorea","Tourette's"],
     "primary_receptors_actions": ["VMAT2 inhibitor (deuterated → longer t½ vs tetrabenazine)"],
     "_notes": "BID. Better tolerated than tetrabenazine."},
    {"generic_name": "Tetrabenazine", "drug_class": "VMAT2 inhibitor — Movement disorders",
     "brand_examples": ["Xenazine"], "key_indications_psych": ["Huntington's chorea","Tourette's (off-label)","Tardive dyskinesia (off-label)"],
     "primary_receptors_actions": ["VMAT2 inhibitor"],
     "_notes": "Older. Black box: depression/suicidality. CYP2D6 dosing."},

    # ── Other / specialty ────────────────────────────────────────────────────
    {"generic_name": "Cyproheptadine", "drug_class": "5-HT2A antagonist / Antihistamine",
     "brand_examples": ["Periactin"], "key_indications_psych": ["Serotonin syndrome (antidote)","SSRI sexual dysfunction (off-label)","Appetite stimulation (anorexia, off-label)","SSRI-induced sweating"],
     "primary_receptors_actions": ["5-HT2A antagonist","H1 antagonist","muscarinic"],
     "_notes": "Antidote for serotonin syndrome (12mg load → 2mg q2h). Sedating."},
    {"generic_name": "Lemborexant", "drug_class": "Sleep aid — DORA",
     "brand_examples": ["Dayvigo"], "key_indications_psych": ["Insomnia"],
     "primary_receptors_actions": ["Orexin receptor 1 + 2 antagonist"],
     "_notes": "Newer DORA. Less hangover than zolpidem."},
    {"generic_name": "Daridorexant", "drug_class": "Sleep aid — DORA",
     "brand_examples": ["Quviviq"], "key_indications_psych": ["Insomnia"],
     "primary_receptors_actions": ["Orexin receptor antagonist"],
     "_notes": "Newest DORA (2022). Short t½."},
]

# Sane defaults applied to entries that omit them
DEFAULTS = {
    "approved_for_pediatric": False,
    "stahl_essential_chapter": None,
    "stahl_essential_pages": [],
    "stahl_pediatric_pages": [],
    "complexity": "medium",
    "study_pair_with_diagnosis": None,
    "_source": "user-supplement-2026-05-10",
}


def main():
    data = json.loads(MED_FILE.read_text(encoding="utf-8"))
    existing = {x["generic_name"].lower() for x in data["medications"]}

    added = 0
    for entry in ADDITIONS:
        key = entry["generic_name"].lower()
        if key in existing:
            print(f"  SKIP (already present): {entry['generic_name']}")
            continue
        merged = {**DEFAULTS, **entry}
        data["medications"].append(merged)
        added += 1
        # ASCII-safe print (Windows cp1255 chokes on Greek/Hebrew/em-dash)
        cls_ascii = entry.get('drug_class', '?')[:50].encode('ascii', 'replace').decode()
        print(f"  + {entry['generic_name']:35} [{cls_ascii}]")

    MED_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    print(f"\nAdded {added} entries. Total now: {len(data['medications'])}")


if __name__ == "__main__":
    main()
