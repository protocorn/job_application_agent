import asyncio
from Agents.persistent_browser_manager import PersistentBrowserManager

async def main():
    manager = PersistentBrowserManager()
    print("Launching...")
    ctx = await manager.launch_persistent_browser('test_fresh_profile', headless=False)
    print("Launched!", ctx)
    await ctx.close()

asyncio.run(main())