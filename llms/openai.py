import dotenv
from google.adk.models.lite_llm import LiteLlm

dotenv.load_dotenv()


MODEL_GPT_5_MINI = LiteLlm(
    model="openai/gpt-5-mini",
)
