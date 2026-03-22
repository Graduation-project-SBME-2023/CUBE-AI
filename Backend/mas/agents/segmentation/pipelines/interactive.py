import asyncio
import logging
from autogen_agentchat.ui import Console
from autogen_agentchat.agents import AssistantAgent, UserProxyAgent
logger = logging.getLogger(__name__)

def create_interactive_agent(model_client, discovery_tools):

    agent = AssistantAgent(
        name="UserFallbackAgent",
        model_client=model_client,
        tools=discovery_tools,
        system_message="""
            You are UserFallbackAgent.

            Your role:
            - Explain available interactive segmentation models.
            - Explain their parameters using discovery tools.
            - Guide the user step-by-step.
            - Answer user questions.

            Rules:
            - You MUST NOT run segmentation or check masks.
            - You MUST wait for user confirmation via UserProxy.
            - Terminate only when user confirm that the task is done.
            """,
            reflect_on_tool_use= True,
            max_tool_iterations=3 # need to optimize
    )

    return agent

def create_user_proxy_agent(model_client):
    agent = UserProxyAgent(name="HumanUser")
    return agent

async def run_interactive_pipeline(
    user_fallback_agent,
    user_proxy,
    labels
):
        logger.info(f"Starting interactive segmentation guidance for labels: {labels}")
        
        # console 
        logger.info("Providing initial guidance to user...")
        await Console(
            user_fallback_agent.run_stream(
                task=(
                    f"Guide the user through interactive segmentation for these labels: {labels}. "
                    "Explain available interactive segmentation models and their parameters. "
                    "answer there question precisely and also provide precise overview unless they ask for details"
                    "Tell the user they can ask questions, or press Enter if they have no more questions."
                    "terminare when they confirm that they make all masks needed you need to check this."
                )
            )
        )

        logger.info("Entering interactive Q&A loop")
        
        loop = asyncio.get_running_loop()

        while True:
            user_input = await loop.run_in_executor(
                None, input, "\nUser (type your question, or press Enter to finish)> "
            )

            if not user_input.strip():
                logger.info("User pressed Enter with no input - terminating session")
                print("\nInteractive session ended.\n")
                break
            
            # termination words
            exit_phrases = ["done", "finish", "finished", "no more", "that's all", "exit", "quit", "bye", "complete"]
            if any(phrase in user_input.lower() for phrase in exit_phrases):
                logger.info(f"User indicated completion: '{user_input}'")
                print("\ Great! Interactive session completed.\n")
                break

            logger.info(f"[User Input] {user_input}")
            
            task_message = (
                f"The user asked: \"{user_input}\"\n\n"
                f"Context: We are guiding them through interactive segmentation for labels: {labels}\n\n"
                "Please provide a concise answer to their specific question. "
                "Do NOT repeat previous answers or conversation history."
            )

            try:
                print("\nUserFallbackAgent> ", end="", flush=True)
                
                full_response = ""
                async for message in user_fallback_agent.run_stream(task=task_message):
                    if hasattr(message, 'content') and message.content:
                        print(message.content, end="", flush=True)
                        full_response += message.content
                
                print("\n") 
                
                logger.info(f"[Agent Response] {full_response}")
                
            except Exception as e:
                logger.error(f"Error during interactive response: {e}", exc_info=True)
                print(f"\n Error: {str(e)}\n")
                break

        logger.info("Interactive session completed.")    