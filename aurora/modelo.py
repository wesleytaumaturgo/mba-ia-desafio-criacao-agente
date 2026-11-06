"""Fábrica padrão do modelo (D-15, D-16): Gemini com retentativa, lido do ambiente."""

import os
import sys

from dotenv import load_dotenv
from google.adk.models.google_llm import Gemini
from google.genai import types

MODELO_PADRAO = "gemini-3.5-flash-lite"


def criar_modelo() -> Gemini:
    """Carrega o `.env`, exige a chave e devolve o Gemini; só roda no caminho real."""
    load_dotenv()
    if not os.environ.get("GOOGLE_API_KEY"):
        print("GOOGLE_API_KEY ausente: preencha no .env ou no ambiente.", file=sys.stderr)
        sys.exit(1)
    return Gemini(
        model=os.environ.get("AURORA_MODELO") or MODELO_PADRAO,
        retry_options=types.HttpRetryOptions(
            attempts=5,
            initial_delay=2,
            max_delay=30,
            http_status_codes=[429, 500, 503],
        ),
    )
