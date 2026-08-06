"""Load .env so integration tests see the same credentials the spike script does."""

from dotenv import load_dotenv

load_dotenv()
