# config.py — claves vía entorno. Copia .env.example a .env.
import os

DB_PATH = os.getenv("DB_PATH", "davogram.db")
SUPERADMIN_USERNAME = os.getenv("SUPERADMIN_USERNAME", "davito_03")

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
COHERE_API_KEY = os.getenv("COHERE_API_KEY", "")
CF_API_TOKEN = os.getenv("CF_API_TOKEN", "")
CF_ACCOUNT_ID = os.getenv("CF_ACCOUNT_ID", "")
HF_TOKEN = os.getenv("HF_TOKEN", "")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
BRAVE_API_KEY = os.getenv("BRAVE_API_KEY", "")

RESUMEN_HORA = int(os.getenv("RESUMEN_HORA", "8"))
RESUMEN_MIN = int(os.getenv("RESUMEN_MIN", "0"))
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
CF_MODEL = os.getenv("CF_MODEL", "@cf/meta/llama-3.1-8b-instruct")

GDRIVE_FOLDER_ID = os.getenv("GDRIVE_FOLDER_ID", "")
GDRIVE_CREDENTIALS_FILE = os.getenv("GDRIVE_CREDENTIALS_FILE", "gdrive_credentials.json")
