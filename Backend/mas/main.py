import asyncio
from core.planner import Planner

async def main():
    planner = Planner()
    result = await planner.run("A CT radiomics model predicts high-risk neuroblastoma patients.", tools_doc="list of modules...")
    print(result)

asyncio.run(main())
