import argparse
import json
import sys

from .core import load_config, pull, push, validate


def main() -> int:
    ap = argparse.ArgumentParser("locsync")
    ap.add_argument("op", choices=["push", "pull", "validate"])
    ap.add_argument("--config", default="locsync.config.json")
    ap.add_argument("--strict", action="store_true",
                    help="validate: fail on untranslated keys too")
    args = ap.parse_args()

    cfg = load_config(args.config)
    if args.op == "push":
        out = push(cfg)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    if args.op == "pull":
        out = pull(cfg)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    out, code = validate(cfg, strict=args.strict)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
