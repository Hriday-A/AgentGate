import argparse

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the AgentGate mock portals")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5055)
    args = parser.parse_args()
    uvicorn.run("agentgate.portals.app:app", host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
