import json


def main() -> None:
    print(json.dumps({"lang": "python", "sum": 20 + 22, "ok": True}, sort_keys=True))


if __name__ == "__main__":
    main()
