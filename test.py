from src.config import get_settings

settings = get_settings()

key = settings.openai_api_key
print("OpenAI API Key:", f"set (...{key[-4:]})" if key else "MISSING")
print("OpenAI Model:", settings.openai_model)
