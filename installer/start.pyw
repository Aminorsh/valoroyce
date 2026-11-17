# What the Valoroyce shortcut runs, with the Python that ships in the installer
# (python\pythonw.exe -I start.pyw). See src/valoroyce/starter.py.
import sys
from pathlib import Path

root = Path(__file__).resolve().parent
sys.path.insert(0, str(root / "src"))

from valoroyce.starter import main  # noqa: E402

sys.exit(main(root))
