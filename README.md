# Shai

Shai (Shell AI) is an Ollama-powered command-line assistant that turns natural
language requests into shell commands tailored to your system. It gathers
system context, asks follow-up questions, and helps you safely run commands or
install missing tools.

## Features
- Streams command suggestions directly into an interactive full-screen menu
  while they are generated.
- Shows per-command status, optional brief explanation, and highlights newly
  added variants.
- Menu actions for each suggestion:
  - **Execute** – run the command as-is.
  - **Exec → Continue** – run, capture output, then get fresh suggestions.
  - **Comment** – refine the suggestion with your feedback.
  - **Explain** – clear-screen view with a step-by-step breakdown of command
    parts.
  - **Variations** – replace the list with alternate commands.
  - **Back** – return to the suggestions list.
- Commands are pre-filled for editing before execution and are appended to your
  shell history.
- Detects missing binaries and offers to install them using your preferred
  package managers, skip installation, or ignore specific tools forever.
- Warns on dangerous commands (e.g. `rm`, `dd`, `mkfs`) and requires
  confirmation unless `-u/--unsafe` is passed.
- Collects rich context for better suggestions: OS, shell, desktop/session,
  `.config` subfolders, current directory path and listing (with a configurable
  max), recent output, and custom extra commands defined in the config.
- Stores a constant part of the system prompt in `prompt.txt` so you can tweak
  how the model behaves.

## Installation
1. Install [Ollama](https://ollama.ai) and start the service:
   ```bash
   ollama serve
   ```
2. Clone this repository and run the CLI as a module:
   ```bash
   git clone <repo-url>
   cd shai
   python -m shai "list large log files"
   ```

## Usage
Run with a natural language request:
```bash
python -m shai "find large files"
```
If launched with no arguments, Shai will ask **What do you want to do?** in a
centered prompt. Use the arrow keys to select a suggestion and press Enter to
open the action menu.

Helpful flags:
- `-n/--num N` – number of suggestions.
- `-e/--explain` or `--no-explain` – control whether brief explanations are
  shown.
- `--model NAME` and `--ctx TOKENS` – override model and context window.
- `-u/--unsafe` – skip dangerous command confirmation.
- `-h/--help` – show all CLI options.

## Configuration
A default config is created at `~/.config/shai/config`. It uses a
TOML‑ish syntax with sections such as:
```toml
[model]
name = "qwen2.5-coder:3b"
ctx = 8192

[suggestions]
n = 3
explain = false

[context]
history_lines = 30
use_stdin = true
cwd_items_max = 40
extras = [
  ["Kernel", "uname -r"],
  ["Window manager", "echo $XDG_CURRENT_DESKTOP"]
]

[pm]
order = "pacman,apt,dnf,zypper,brew,flatpak,snap,yay,paru"

[prompt]
file = "prompt.txt"
```
Key options:
- `extras` – list of `[description, command]` pairs executed once at startup.
- `pm.order` – priority of package managers to search when binaries are missing.
- `prompt.file` – path (relative to config dir) for the system prompt text.
- `ignored.txt` – list of binaries to skip in future install prompts.

## License
MIT – see [LICENSE](LICENSE).

