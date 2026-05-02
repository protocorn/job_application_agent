import asyncio
import logging
import os
from pathlib import Path

# Setup logging
logging.basicConfig(level=logging.DEBUG)

# Add Agents to path so we can import
import sys
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from Agents.persistent_browser_manager import PersistentBrowserManager

async def main():
    manager = PersistentBrowserManager()
    print(f"Base dir: {manager.base_dir}")
    try:
        context = await manager.launch_persistent_browser(
            user_id="de18962e-29c6-4227-9b0e-28287fdbef3e",
            headless=False
        )
        print("Success!")
        await context.close()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
