import argparse
import json
from urllib.error import URLError
from urllib.request import urlopen

from . import CONTRACT_VERSION, SERVICE_NAME
from .config import AgentConfig
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


def main():
    parser = argparse.ArgumentParser(description="Revit AI Area Assistant local Agent")
    parser.add_argument("--check", action="store_true", help="print readiness and exit")
    parser.add_argument("--serve", action="store_true", help="serve the loopback HTTP API")
    args = parser.parse_args()
    if args.check:
        print(json.dumps(readiness_payload(), ensure_ascii=False, sort_keys=True))
        return
    if args.serve:
        serve()
        return
    parser.error("no action selected; use --check or --serve")


if __name__ == "__main__":
    main()
