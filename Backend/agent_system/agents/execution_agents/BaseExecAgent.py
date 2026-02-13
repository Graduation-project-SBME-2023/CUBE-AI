from autogen_agentchat.agents import AssistantAgent
import logging

class BaseExecAgent(AssistantAgent):
    async def on_messages(self, messages, **kwargs):
        logger = logging.getLogger()
        logger.info(f"[START] Agent {self.name}")
        result = await super().on_messages(messages, **kwargs)
        logger.info(f"[END] Agent {self.name}")
        return result