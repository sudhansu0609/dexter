"""Load .env before any module reads its configuration.

Only main.py loaded .env, so every other entry point - the eval harness, the
pytest suites, any standalone script - saw unset variables and silently fell
back to hardcoded defaults. The visible symptom: the function-calling eval
scored a 'llama3.2' that is not installed on this machine (0/15) while the
configured qwen2.5:7b scores 13/15.

Importing this module first makes configuration identical however the code is
entered. load_dotenv() is idempotent and never overrides variables already set
in the real environment, so repeated imports and explicit overrides both work.
"""

import os

_ENV_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"
)

try:
    from dotenv import load_dotenv

    load_dotenv(_ENV_PATH)
except ImportError:  # python-dotenv is optional; defaults still apply
    pass
