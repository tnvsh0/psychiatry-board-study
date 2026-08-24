import sys, os, time, json
os.environ["NOTEBOOKLM_PROFILE"] = "bulk"
sys.path.insert(0, "scripts")
import daily_study as d
NB = "feedf1b3-742c-4fbd-9ba5-63c6479d019d"
FID = "1RwzuraeBTTwM-DvQSNWLCLORUABHKA5n"
r = d.nb_run(["source", "add-drive", FID, "Kaplan & Sadock Synopsis",
              "-n", NB, "--mime-type", "pdf", "--json"], timeout=300)
print("add rc=", r.returncode, flush=True)
try:
    src = json.loads(r.stdout[r.stdout.index("{"):]).get("source", {})
    print("  type=", src.get("type"), "drive=", bool(src.get("drive_file_id")), flush=True)
except Exception:
    print(" ", (r.stdout or "")[:400], flush=True)
for i in range(24):
    time.sleep(20)
    k = [s.get("status") for s in (d._list_sources(NB) or [])
         if "aplan" in (s.get("title") or "")]
    print(f"  +{(i+1)*20:4}s kaplan={k}", flush=True)
    if k and k[0] in ("ready", "error"):
        break
print("FINAL", k, flush=True)
