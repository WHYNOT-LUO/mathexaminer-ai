"""Second entry point for the same demo, used by the backup app on Streamlit Community Cloud.

Community Cloud allows one app per repository, branch and main file, so the backup copy
(mathexaminer-ai-backup.streamlit.app) points here instead of at demo_app.py.
"""

import runpy
from pathlib import Path

runpy.run_path(str(Path(__file__).with_name("demo_app.py")), run_name="__main__")
