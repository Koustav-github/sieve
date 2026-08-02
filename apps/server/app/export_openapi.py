import json
import sys

from app.main import app


def main() -> None:
    schema = app.openapi()
    output = sys.argv[1] if len(sys.argv) > 1 else "openapi.json"
    with open(output, "w") as f:
        json.dump(schema, f, indent=2)


if __name__ == "__main__":
    main()
