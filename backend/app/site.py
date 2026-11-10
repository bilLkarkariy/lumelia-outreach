"""Serve the compiled UI and the API from a single origin (single-container deployments)."""

import os
import re
from pathlib import Path

from fastapi.staticfiles import StaticFiles

from .main import app


class WorkspaceStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        # Only known workspace UI routes resolve to the shell; missing API/asset
        # paths keep their normal 404 response.
        if path.rstrip("/") in {
            "contacts",
            "tasks",
            "campaigns",
            "settings",
            "offers",
            "companies",
            "qualification",
            "inbox",
            "calendar",
            "quality",
            "analytics",
            "alerts",
            "agent",
        } or re.fullmatch(r"(?:contacts|companies|campaigns|sequences|inbox)/[a-zA-Z0-9-]+/?", path):
            path = "index.html"
        return await super().get_response(path, scope)


static_dir = Path(os.environ.get("LUMELIA_STATIC_DIR", Path(__file__).resolve().parent.parent / "static"))
app.mount("/", WorkspaceStaticFiles(directory=static_dir, html=True), name="workspace-ui")
