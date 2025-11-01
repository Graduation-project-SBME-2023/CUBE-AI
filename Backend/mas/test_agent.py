import sys
import asyncio
from Backend.mas.agents.feature_extraction import RadiomicFeatureExtractorAndModellingAgent

async def main():
    # # read instruction from command-line argument
    # if len(sys.argv) > 1:
    #     instruction = " ".join(sys.argv[1:])
    # else:
    #     instruction = "Extract radiomic features from CT scan and segmentation mask"

    agent = RadiomicFeatureExtractorAndModellingAgent()
    result = await agent.run(r"D:\GP_2026\Github\agenticsystem\OHIF-Standalone\Backend\mas\memory\workspace\configurations.json")
if __name__ == "__main__":
    asyncio.run(main())
