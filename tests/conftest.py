"""Load .env so integration tests see the same credentials the spike script does."""

import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# `callsheet` is installed; `bench` is not — it is a harness, not a library, and
# packaging it would ship the benchmark to anyone who pip-installs the product.
# `python -m pytest` happens to put the repo root on the path and a bare `pytest`
# does not, so without this the ablation's tests pass or fail depending on how
# the suite was invoked.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
