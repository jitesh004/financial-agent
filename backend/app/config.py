import os
from pathlib import Path
from dotenv import load_dotenv

_root_dir = Path(__file__).resolve().parents[2]
load_dotenv(_root_dir / '.env')

class Config:
    LLM_PROVIDER = os.environ.get('LLM_PROVIDER', 'gemini').lower()
    
    GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
    GEMINI_MODEL_FAST = os.environ.get('GEMINI_MODEL_FAST', 'gemini-2.5-flash')
    GEMINI_MODEL_STRONG = os.environ.get('GEMINI_MODEL_STRONG', 'gemini-2.5-pro')

    AZURE_OPENAI_ENDPOINT = os.environ.get('AZURE_OPENAI_ENDPOINT')
    AZURE_OPENAI_API_KEY = os.environ.get('AZURE_OPENAI_API_KEY')
    AZURE_OPENAI_API_VERSION = os.environ.get('AZURE_OPENAI_API_VERSION', '2024-02-15-preview')
    AZURE_OPENAI_DEPLOYMENT_FAST = os.environ.get('AZURE_OPENAI_DEPLOYMENT_FAST', 'gpt-4o-mini')
    AZURE_OPENAI_DEPLOYMENT_STRONG = os.environ.get('AZURE_OPENAI_DEPLOYMENT_STRONG', 'gpt-4o')

config = Config()
