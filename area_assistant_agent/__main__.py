import argparse
import json
import os
from pathlib import Path
import sys
from urllib.error import URLError
from urllib.request import urlopen

from . import CONTRACT_VERSION, SERVICE_NAME
from .binding_state_store import BindingStateStore
from .config import AgentConfig
from .document_status_runtime import resolve_document_status
from .persistence import SessionRepository
from .rvt_mcp_gateway import McpStdioClient
from .server import create_server


def readiness_payload():
    return {
        "contract_version": CONTRACT_VERSION,
        "service": SERVICE_NAME,
        "status": "ready",
    }


def _existing_agent_is_ready(config):
    try:
        with urlopen(
            "http://127.0.0.1:{}/health".format(config.port), timeout=0.5
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return (
            payload.get("contract_version") == CONTRACT_VERSION
            and payload.get("payload", {}).get("service") == SERVICE_NAME
        )
    except (OSError, ValueError, URLError):
        return False


def serve():
    config = AgentConfig.from_environment()
    if _existing_agent_is_ready(config):
        print(json.dumps({"status": "already-running", "port": config.port}))
        return
    try:
        server = create_server(config)
    except OSError:
        if _existing_agent_is_ready(config):
            print(json.dumps({"status": "already-running", "port": config.port}))
            return
        raise
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def document_status():
    request = json.load(sys.stdin)
    request_id = str(request.get("request_id", "document-status"))
    try:
        if (
            request.get("contract_version") != CONTRACT_VERSION
            or request.get("message_type") != "request"
            or request.get("action") != "revit.document_status"
            or not isinstance(request.get("payload"), dict)
        ):
            raise ValueError("unsupported or invalid document status request envelope")
        payload = request["payload"]
        with McpStdioClient() as client:
            response = resolve_document_status(
                request_id=request_id,
                current_payload=payload["current_document"],
                previous_payload=payload.get("previous_document"),
                previous_pause_reason=payload.get("previous_pause_reason"),
                authorized_document_path=os.environ.get(
                    "AI_AREA_ASSISTANT_TEST_DOCUMENT", ""
                ),
                client=client,
                binding_store=BindingStateStore(),
            )
        print(json.dumps(response, ensure_ascii=False, sort_keys=True))
        return 0
    except (KeyError, RuntimeError, ValueError) as error:
        response = {
            "contract_version": CONTRACT_VERSION,
            "message_type": "error",
            "request_id": request_id,
            "code": "document_status_unavailable",
            "message": str(error),
            "retryable": True,
            "details": {},
        }
        print(json.dumps(response, ensure_ascii=False, sort_keys=True))
        return 1


def main():
    parser = argparse.ArgumentParser(description="Revit AI Area Assistant local Agent")
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--check", action="store_true", help="print readiness and exit")
    actions.add_argument("--serve", action="store_true", help="serve the loopback HTTP API")
    actions.add_argument(
        "--document-status",
        action="store_true",
        help="read a pyRevit snapshot from stdin and verify it through rvt-mcp",
    )
    actions.add_argument(
        "--show-data-root",
        metavar="PROJECT_DIRECTORY",
        help="print the resolved external data directory and exit",
    )
    args = parser.parse_args()
    if args.check:
        print(json.dumps(readiness_payload(), ensure_ascii=False, sort_keys=True))
        return 0
    if args.serve:
        serve()
        return 0
    if args.show_data_root:
        repository = SessionRepository(Path(args.show_data_root))
        print(
            json.dumps(
                {"data_root": str(repository.data_root)},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    return document_status()


if __name__ == "__main__":
    sys.exit(main())
