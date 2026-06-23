"""One-off: rebuild the RELATIONAL notebook offline, gate flipped in-process only.
Leaves systemd env untouched; interior notebook untouched. Run with the venv."""
import asyncio
import httpx

from config.settings import NVIDIA_API_KEY
import cognition.knowledge_builder as kb

MAX_PASSES = 50  # safety leash; 104 turns / 12 per pass ~= 9 passes

async def main() -> None:
    if not NVIDIA_API_KEY:
        raise SystemExit("No NVIDIA_API_KEY in env — load .env before running.")
    kb.KNOWLEDGE_ENABLED = True          # in-process ONLY; does not touch systemd
    client = httpx.AsyncClient()
    try:
        total = 0
        for i in range(1, MAX_PASSES + 1):
            n = await kb.run(client, notebook="relational")
            print(f"pass {i}: processed {n} source turns")
            total += n
            if n == 0:
                break
        print(f"DONE — {total} turns processed across the rebuild")
    finally:
        await client.aclose()

if __name__ == "__main__":
    asyncio.run(main())
