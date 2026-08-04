import argparse
import json

from . import CONTRACT_VERSION, SERVICE_NAME


def readiness_payload():
    return {
        "contract_version": CONTRACT_VERSION,
        "service": SERVICE_NAME,
        "status": "ready",
    }


def main():
    parser = argparse.ArgumentParser(description="Revit AI Area Assistant local Agent")
    parser.add_argument("--check", action="store_true", help="print readiness and exit")
    args = parser.parse_args()
    if args.check:
        print(json.dumps(readiness_payload(), ensure_ascii=False, sort_keys=True))
        return
    parser.error("no action selected; use --check")


if __name__ == "__main__":
    main()
