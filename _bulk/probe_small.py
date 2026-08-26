import asyncio, os, sys, time
os.environ.setdefault("NOTEBOOKLM_PROFILE", "bulk")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from notebooklm import NotebookLMClient

async def main():
    async with await NotebookLMClient.from_storage() as c:
        nb = await c.notebooks.create(f"CHATPROBE {int(time.time())}")
        nid = nb.id if hasattr(nb, "id") else nb["id"]
        print("fresh notebook:", nid)
        await c.sources.add_text(nid, "probe",
                                 "The sky is blue. The secret number is 42.")
        print("source added; waiting 30s for indexing")
        await asyncio.sleep(30)
        try:
            r = await c.chat.ask(nid, "What is the secret number?")
            print("SMALL NOTEBOOK ANSWER:", str(r)[:200])
        except Exception as e:
            print("SMALL NOTEBOOK RAISED:", type(e).__name__, str(e)[:120])
        await c.notebooks.delete(nid)
        print("cleaned up")

asyncio.run(main())
