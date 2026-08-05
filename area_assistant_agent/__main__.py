import argparse
import json
from pathlib import Path

from . import CONTRACT_VERSION, SERVICE_NAME
from .persistence import SessionRepository


def readiness_payload():
    return {
        "contract_version": CONTRACT_VERSION,
        "service": SERVICE_NAME,
        "status": "ready",
    }


def main():
    parser = argparse.ArgumentParser(description="Revit AI Area Assistant local Agent")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--check", action="store_true", help="print readiness and exit")
    actions.add_argument(
        "--show-data-root",
        metavar="PROJECT_DIRECTORY",
        help="print the resolved external data directory and exit",
    )
    args = parser.parse_args()
    if args.check:
        print(json.dumps(readiness_payload(), ensure_ascii=False, sort_keys=True))
        return
    if args.show_data_root:
        repository = SessionRepository(Path(args.show_data_root))
        print(
            json.dumps(
                {"data_root": str(repository.data_root)},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return
    parser.error("no action selected; use --check or --show-data-root")


if __name__ == "__main__":
    main()
