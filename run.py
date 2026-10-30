"""Entry point for the ACE Developer Agent desktop tool."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent.gui import main

if __name__ == "__main__":
    main()
