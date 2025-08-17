import re, shutil
from typing import List, Dict

# Bash built-ins and shell keywords that shouldn't be treated as external commands
BUILTINS = {
    "cd", "source", ".", "alias", "export", "unset", "set", "exit",
    "echo", "readonly", "type", "hash", "bg", "fg"
}

def extract_commands(cmd: str) -> List[str]:
    parts = re.split(r'[|;&]', cmd)
    cmds, seen = [], set()
    for p in parts:
        toks = p.strip().split()
        if not toks:
            continue
        first = toks[1] if toks[0] == "sudo" and len(toks) > 1 else toks[0]
        if first in BUILTINS:
            continue
        if first not in seen:
            seen.add(first)
            cmds.append(first)
    return cmds

def which_map(binaries: List[str]) -> Dict[str, str]:
    return {b: (shutil.which(b) or "") for b in binaries}

