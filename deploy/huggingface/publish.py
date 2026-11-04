"""Publish (or update) the backup demo as a Hugging Face Space.

    .venv/bin/pip install huggingface_hub
    .venv/bin/python deploy/huggingface/publish.py

It asks for a Hugging Face access token with write permission (typed, not shown), creates the
Space <your-username>/mathexaminer-ai-demo if it does not exist, and uploads only what the demo
needs: the app's code, the demo backend and its samples, the Streamlit theme, and this folder's
Dockerfile and Space README. Secrets are never uploaded; the demo needs none.
"""

from __future__ import annotations

import getpass
import os
import shutil
import sys
import tempfile
from pathlib import Path

from huggingface_hub import HfApi

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
SPACE_NAME = "mathexaminer-ai-demo"
COPY = ["app.py", "requirements.txt", "LICENSE", "mathexaminer", "demo", ".streamlit/config.toml"]
SKIP = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store")


def stage(target: Path) -> None:
    for rel in COPY:
        src, dst = ROOT / rel, target / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dst, ignore=SKIP)
        else:
            shutil.copy2(src, dst)
    shutil.copy2(HERE / "Dockerfile", target / "Dockerfile")
    shutil.copy2(HERE / "SPACE_README.md", target / "README.md")
    leaked = [p for p in target.rglob("*") if p.name in {"secrets.toml", ".env"}]
    if leaked:
        raise SystemExit(f"refusing to upload secrets: {leaked}")


def main() -> int:
    token = os.environ.get("HF_TOKEN") or getpass.getpass("Hugging Face access token (write): ").strip()
    api = HfApi(token=token)
    user = api.whoami()["name"]
    repo_id = f"{user}/{SPACE_NAME}"
    api.create_repo(repo_id, repo_type="space", space_sdk="docker", exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        stage(Path(tmp))
        api.upload_folder(
            repo_id=repo_id, repo_type="space", folder_path=tmp,
            commit_message="Publish the MathExaminer AI demo",
            delete_patterns=["mathexaminer/**", "demo/**"],  # drop files removed from the code
        )
    host = f"{user}-{SPACE_NAME}".lower().replace("_", "-").replace(".", "-")
    print(f"Published. The Space builds for a few minutes, then runs at:\n"
          f"  https://huggingface.co/spaces/{repo_id}\n  https://{host}.hf.space")
    return 0


if __name__ == "__main__":
    sys.exit(main())
