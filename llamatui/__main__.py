"""Entry point: parse args and launch the TUI."""

from __future__ import annotations

import argparse
import warnings

# agent_framework marks SkillResource/MemoryStore as experimental and emits an
# ExperimentalWarning the moment those classes are imported. We knowingly depend
# on them, so silence the noise. The message filter must be registered *before*
# agent_framework is first imported (including the category import below, which
# itself pulls in the package), so it goes first.
warnings.filterwarnings("ignore", message=r".*is experimental and may change.*")
try:
    from agent_framework._feature_stage import FeatureStageWarning

    warnings.filterwarnings("ignore", category=FeatureStageWarning)
except Exception:  # pragma: no cover - message filter above already covers it
    pass

from .app import Config, LlamaTUI


def cli_overrides(args) -> dict:
    """Map parsed args to the precedence dict settings.load expects (unset → None sentinel)."""
    return {
        "thinking_budget": args.thinking_budget,
        "temperature": args.temp,
        "top_p": args.top_p,
        "max_tokens": args.max_tokens,
        "voice_mode": args.voice_mode,
        "default_workspace": args.workspace,
    }


def main() -> None:
    ap = argparse.ArgumentParser(
        prog="llamatui",
        description="A terminal UI for a local llama-server, built on the Microsoft Agent Framework.",
    )
    ap.add_argument("--url", default="http://127.0.0.1:8080", help="llama-server base URL")
    ap.add_argument("--model", default="local", help="model id (auto-detected from the server if possible)")
    ap.add_argument("--system", default=None, help="initial system prompt")
    ap.add_argument("--temp", type=float, default=None, help="sampling temperature (default: 0.7)")
    ap.add_argument("--max-tokens", type=int, default=None, help="max tokens to generate (default: 32000)")
    ap.add_argument("--top-p", type=float, default=None, help="nucleus sampling probability (default: off)")
    ap.add_argument(
        "--thinking-budget", type=int, default=None,
        help="max thinking tokens (default: 8192; N>0 budget, 0 off, -1 unlimited). "
             "Honored only if llama-server was started without --reasoning-budget.",
    )
    ap.add_argument("--voice-mode", choices=["toggle", "hold"], default=None,
                    help="dictation input mode (default: toggle)")
    ap.add_argument("--db", default=None, help="path to the conversations SQLite file")
    ap.add_argument("--no-web", action="store_true", help="disable the Exa web-search tool")
    ap.add_argument("--no-memory", action="store_true", help="disable the persistent memory tool")
    ap.add_argument("--no-voice", action="store_true", help="disable voice dictation (Ctrl+R)")
    ap.add_argument("--whisper-bin", default=None, help="path to whisper-server (default: whisper/whisper-server.exe, then PATH)")
    ap.add_argument("--whisper-model", default=None, help="path to the whisper ggml model (default: whisper/ggml-small.en.bin)")
    ap.add_argument("--whisper-url", default=None, help="use an already-running whisper-server at this URL instead of spawning one")
    ap.add_argument("--setup-voice", action="store_true",
                    help="download whisper-server + model into the user-data dir, then exit")
    ap.add_argument("--workspace", default=None,
                    help="default workspace root for new chats (default: cwd)")
    ap.add_argument("--no-fs", action="store_true",
                    help="disable the filesystem tools")
    args = ap.parse_args()

    if args.setup_voice:
        from . import paths, setup_voice
        dest = paths.default_whisper_dir()
        print(f"Fetching whisper-server + model into {dest} ...")
        exe = setup_voice.fetch_whisper(dest)
        print(f"Done. whisper-server at {exe}")
        return

    base_url = args.url.rstrip("/")
    if not base_url.endswith("/v1"):
        base_url += "/v1"

    config = Config(
        url=base_url,
        model=args.model,
        system=args.system,
        db_path=args.db,
        web=not args.no_web,
        memory=not args.no_memory,
        voice=not args.no_voice,
        whisper_bin=args.whisper_bin,
        whisper_model=args.whisper_model,
        whisper_url=args.whisper_url,
        fs=not args.no_fs,
        workspace=args.workspace,
    )
    LlamaTUI(config, cli_overrides=cli_overrides(args)).run()


if __name__ == "__main__":
    main()
