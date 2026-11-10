"""Private runner HTTP client; the service credential is never returned to the model."""

import json
import os
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def config():
    if os.environ.get("LUMELIA_AGENT_TOKEN"):
        return {
            "url": os.environ.get("LUMELIA_URL", "http://127.0.0.1:8000"),
            "token": os.environ["LUMELIA_AGENT_TOKEN"],
            "runtime": os.environ.get("LUMELIA_AGENT_RUNTIME", "/tmp/lumelia-agent"),
        }
    raise RuntimeError("Set LUMELIA_AGENT_TOKEN to the runner credential issued by the application.")


def call(path, method="GET", body=None, lease=None, usage_token=None):
    cfg = config()
    headers = {
        "Authorization": "Bearer " + cfg["token"],
        "Content-Type": "application/json",
        "X-Agent-Host": os.environ.get("LUMELIA_AGENT_HOST", "local"),
    }
    if usage_token:
        headers["X-Agent-Usage"] = usage_token
    if lease:
        headers["X-Agent-Lease"] = lease
    request = Request(
        cfg["url"].rstrip("/") + "/api/agent/runtime" + path,
        data=None if body is None else json.dumps(body).encode(),
        headers=headers,
        method=method,
    )
    try:
        with urlopen(request, timeout=45) as response:
            return json.load(response)
    except HTTPError as error:
        try:
            detail = json.loads(error.read()).get("detail", "Request refused")
        except ValueError:
            detail = "Request refused"
        raise RuntimeError(json.dumps({"status": error.code, "detail": detail}, ensure_ascii=False)) from None
