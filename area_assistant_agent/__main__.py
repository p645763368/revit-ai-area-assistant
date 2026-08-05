import argparse
import json
import os
import sys

from . import CONTRACT_VERSION, SERVICE_NAME
from .document_status_runtime import resolve_document_status
from .rvt_mcp_gateway import McpStdioClient


def readiness_payload():
    return {
        "contract_version": CONTRACT_VERSION,
        "service": SERVICE_NAME,
        "status": "ready",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Revit AI Area Assistant local Agent")
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--check", action="store_true", help="print readiness and exit")
    actions.add_argument(
        "--document-status",
        action="store_true",
        help="read a pyRevit snapshot from stdin and verify it through rvt-mcp",
    )
    args = parser.parse_args()
    if args.check:
        print(json.dumps(readiness_payload(), ensure_ascii=False, sort_keys=True))
        return 0

    request = json.load(sys.stdin)
    request_id = str(request.get("request_id", "document-status"))
    try:
        with McpStdioClient() as client:
            response = resolve_document_status(
                request_id=request_id,
                current_payload=request["current_document"],
                previous_payload=request.get("previous_document"),
                authorized_document_path=os.environ.get(
                    "AI_AREA_ASSISTANT_TEST_DOCUMENT", ""
                ),
                client=client,
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


if __name__ == "__main__":
    sys.exit(main())
