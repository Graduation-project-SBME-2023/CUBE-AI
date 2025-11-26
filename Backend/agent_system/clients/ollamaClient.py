from autogen_ext.models.ollama import OllamaChatCompletionClient

class OllamaClient(OllamaChatCompletionClient):
    def __init__(self, model = "gpt-oss:120b-cloud", model_info={"vision": False, "function_calling": True, "json_output": True, "structured_output": True}):
        super().__init__()
        self.model=model,
        self.model_info=model_info