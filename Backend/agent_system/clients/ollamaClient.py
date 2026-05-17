from autogen_ext.models.ollama import OllamaChatCompletionClient


class OllamaClient(OllamaChatCompletionClient):
    def __init__(self,
                 model="gpt-oss:120b-cloud",
                 model_info=None):

        if model_info is None:
            model_info = {
                "vision": False,
                "function_calling": True,
                "json_output": True,
                "structured_output": True,
                "family": "unknown",
                
            }
        super().__init__(model=model, model_info=model_info, options={"num_ctx": 16384})
        self._model = model
        self._model_info = model_info
