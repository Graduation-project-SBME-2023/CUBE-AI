import sys
import asyncio
from mas.agents.segmentation_agent import SegmentationModule
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

async def main():

    agent = SegmentationModule()
    await agent.initialize()
    result = await agent.run(r"D:\GP_2026\Github\agenticsystem\OHIF-Standalone\Backend\mas\memory\workspace\configurations.json")
if __name__ == "__main__":
    asyncio.run(main())
