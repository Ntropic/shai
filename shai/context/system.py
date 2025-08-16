"""System context: detect OS, shell, hardware basics, editor prefs."""
import platform, os, shutil, subprocess

def gather_context(stdin_snippet:str="", hist_n:int=20) -> dict:
    # OS/distro
    try:
        import distro
        distro_name = distro.name(pretty=True)
    except Exception:
        distro_name = " ".join(platform.uname())

    # editor preference
    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL")

    return {
        "os": platform.system(),
        "distro": distro_name,
        "shell": os.environ.get("SHELL"),
        "cpu": platform.processor(),
        "editor": editor,
        "stdin": stdin_snippet[-2000:],
    }

