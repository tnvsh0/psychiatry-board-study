"""Map exactly which NotebookLM operations work right now."""
import asyncio, os, sys, time
os.environ.setdefault("NOTEBOOKLM_PROFILE", "bulk")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import logging; logging.disable(logging.WARNING)
from notebooklm import NotebookLMClient

async def probe(label, coro):
    t0 = time.time()
    try:
        r = await coro
        print(f"  OK    {label:34} {time.time()-t0:5.1f}s")
        return r
    except Exception as e:
        print(f"  FAIL  {label:34} {time.time()-t0:5.1f}s  {type(e).__name__}: {str(e)[:70]}")
        return None

async def main():
    async with await NotebookLMClient.from_storage() as c:
        nbs = await probe("list notebooks", c.notebooks.list())
        if not nbs: return
        nb = nbs[0].id
        await probe("list sources", c.sources.list(nb))
        tmp = await probe("create notebook", c.notebooks.create(f"RPCPROBE {int(time.time())}"))
        tid = getattr(tmp, "id", None)
        if tid:
            await probe("add text source", c.sources.add_text(tid, "p", "The number is 42."))
            await probe("chat / ask", c.chat.ask(tid, "What number?"))
            for kind, fn in (("generate audio", c.artifacts.generate_audio),
                             ("generate quiz", c.artifacts.generate_quiz),
                             ("generate flashcards", c.artifacts.generate_flashcards)):
                await probe(kind, fn(tid))
            await probe("list artifacts", c.artifacts.list(tid))
            await probe("delete notebook", c.notebooks.delete(tid))

asyncio.run(main())
