import sys
import asyncio
from Backend.mas.agents.feature_extraction import RadiomicFeatureExtractorAndModellingAgent

async def main():

    agent = SegmentationModule()
    await agent.initialize()
    result = await agent.run(r"D:\GP_2026\Github\agenticsystem\OHIF-Standalone\Backend\mas\memory\workspace\configurations.json")
if __name__ == "__main__":
    asyncio.run(main())
