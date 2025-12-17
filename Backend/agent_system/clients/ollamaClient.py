from autogen_ext.models.ollama import OllamaChatCompletionClient

class OllamaClient(OllamaChatCompletionClient):
    def __init__(self,
                #  model="deepseek-v3.1:671b-cloud",
                 model="gpt-oss:120b-cloud",
                 model_info=None, 
                 api_key = "9044a6e8edfd4bdf88c2027c09ecdeb7.jQPdSXPlhcffePwxx_1OQ7nd"):

        if model_info is None:
            model_info = {
                "vision": False,
                "function_calling": True,
                "json_output": True,
                "structured_output": True,
                "family": "unknown",
                
            }
        super().__init__(model=model, model_info=model_info)
        self._model = model
        self._model_info = model_info
