from __future__ import annotations

import argparse
import json
import shlex
import sys
from pathlib import Path


def parse_curl(command: str) -> dict:
    tokens = shlex.split(command)
    if not tokens or tokens[0] != "curl":
        raise ValueError("Input must start with curl")

    method = "GET"
    url = None
    headers: dict[str, str] = {}
    body = None
    cookie = None

    i = 1
    while i < len(tokens):
        token = tokens[i]
        if token in ("-X", "--request"):
            i += 1
            method = tokens[i].upper()
        elif token in ("-H", "--header"):
            i += 1
            raw = tokens[i]
            if ":" in raw:
                key, value = raw.split(":", 1)
                headers[key.strip()] = value.strip()
        elif token in ("--data", "--data-raw", "--data-binary", "-d"):
            i += 1
            body = tokens[i]
            if method == "GET":
                method = "POST"
        elif token in ("-b", "--cookie", "--cookie-jar"):
            i += 1
            cookie = tokens[i]
        elif token.startswith("http://") or token.startswith("https://"):
            url = token
        i += 1

    if not url:
        raise ValueError("No URL found in curl command")

    if cookie:
        headers["Cookie"] = cookie

    override = {"method": method, "url": url, "headers": headers}
    if body:
        try:
            override["json_body"] = json.loads(body)
        except json.JSONDecodeError:
            override["body"] = body
    return override


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert DevTools Copy as cURL to request_overrides/<platform>.json")
    parser.add_argument("platform", choices=["trae", "minimax", "claude", "codex", "kimi", "deepseek"])
    parser.add_argument("--input", help="File containing the copied curl command. Defaults to stdin.")
    args = parser.parse_args()

    command = Path(args.input).read_text() if args.input else sys.stdin.read()
    override = parse_curl(command)

    out_dir = Path("request_overrides")
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"{args.platform}.json"
    out_path.write_text(json.dumps(override, ensure_ascii=False, indent=2))

    header_names = ", ".join(sorted(override.get("headers", {}).keys()))
    print(f"Wrote {out_path}")
    print(f"Method: {override['method']}")
    print(f"Headers: {header_names}")
    if args.platform == "trae" and "authorization" not in {key.lower() for key in override.get("headers", {})}:
        print("WARNING: Trae override is missing the authorization header; the API will likely keep returning 401.")
    if args.platform == "trae" and override.get("json_body") != {"require_usage": True}:
        print('WARNING: Trae override is missing json body {"require_usage": true}.')


if __name__ == "__main__":
    main()
