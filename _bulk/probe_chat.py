"""Capture the RAW chat response so the parse failure can be seen, not guessed."""
import asyncio, os, sys
os.environ.setdefault("NOTEBOOKLM_PROFILE", "bulk")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from notebooklm._chat import wire

real = wire.parse_streaming_chat_response
def spy(text):
    print(f"--- RAW RESPONSE: {len(text)} chars ---")
    print(repr(text[:1500]))
    print("--- END RAW ---")
    return real(text)
wire.parse_streaming_chat_response = spy
import notebooklm._chat.api as api
api.parse_streaming_chat_response = spy

from notebooklm import NotebookLMClient

async def main():
    async with await NotebookLMClient.from_storage() as c:
        nbs = await c.notebooks.list()
        print(f"{len(nbs)} notebooks; using {nbs[0].id}")
        try:
            r = await c.chat.ask(nbs[0].id, "Say OK.")
            print("ANSWER:", str(r)[:200])
        except Exception as e:
            print("RAISED:", type(e).__name__, str(e)[:150])

asyncio.run(main())
