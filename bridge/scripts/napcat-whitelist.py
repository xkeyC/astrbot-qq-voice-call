"""Adds the bridge to, or removes it from, NapCat's plugin whitelist.

Since v4.18.7 NapCat loads only the plugins named in a set built into
napcat.mjs. `add` inserts this plugin's name into that set, with a marker;
`remove` deletes exactly that insertion, so a NapCat upgrade in between is
never rolled back. NapCat's sensitive-keyword scan of plugin code still runs.

usage: napcat-whitelist.py {add|remove} /path/to/napcat.mjs
"""

import os
import re
import sys
from pathlib import Path

PLUGIN = "napcat-plugin-astrbot-qq-voice-call"
ENTRY = f'"{PLUGIN}" /* ASTRBOT_QQ_CALL_WHITELIST */, '
# The whitelist is the Set whose first entry is the builtin plugin.
WHITELIST = re.compile(r'(new Set\(\[\s*)("napcat-plugin-builtin",)')


def write(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="") as file:
            file.write(text)
        temporary.chmod(path.stat().st_mode & 0o7777)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] not in ("add", "remove"):
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    action, path = sys.argv[1], Path(sys.argv[2])
    with path.open(encoding="utf-8", newline="") as file:
        text = file.read()
    if action == "remove":
        if ENTRY in text:
            write(path, text.replace(ENTRY, ""))
            print("removed the bridge from the NapCat plugin whitelist")
        return 0
    if ENTRY in text:
        return 0
    if "isOfficialPlugin(" not in text:
        return 0  # a NapCat without the whitelist loads any plugin
    matches = WHITELIST.findall(text)
    if len(matches) != 1:
        print(
            "NapCat's plugin whitelist was not found where expected; "
            "this NapCat version is not supported by the installer",
            file=sys.stderr,
        )
        return 1
    write(path, WHITELIST.sub(lambda m: m.group(1) + ENTRY + m.group(2), text))
    print("added the bridge to the NapCat plugin whitelist")
    return 0


if __name__ == "__main__":
    sys.exit(main())
