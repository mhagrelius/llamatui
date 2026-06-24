"""Entry point: parse args and launch the TUI."""

from __future__ import annotations

import argparse

from .app import Config, LlamaTUI


def main() -> None:
    ap = argparse.ArgumentParser(
        prog="llamatui",
        description="A terminal UI for a local llama-server, built on the Microsoft Agent Framework.",
    )
    ap.add_argument("--url", default="http://127.0.0.1:8080", help="llama-server base URL")
    ap.add_argument("--model", default="local", help="model id (auto-detected from the server if possible)")
    ap.add_argument("--system", default=None, help="initial system prompt")
    ap.add_argument("--temp", type=float, default=0.7, help="sampling temperature")
    ap.add_argument("--max-tokens", type=int, default=32000, help="max tokens to generate")
    ap.add_argument("--top-p", type=float, default=None, help="nucleus sampling probability")
    ap.add_argument(
        "--thinking-budget", type=int, default=8192,
        help="max thinking tokens before the server forces the answer (N>0 budget, 0 off, -1 unlimited)",
    )
    ap.add_argument("--db", default=None, help="path to the conversations SQLite file")
    ap.add_argument("--no-web", action="store_true", help="disable the Exa web-search tool")
    ap.add_argument("--no-memory", action="store_true", help="disable the persistent memory tool")
    ap.add_argument("--no-voice", action="store_true", help="disable voice dictation (Ctrl+R)")
    ap.add_argument("--whisper-bin", default=None, help="path to whisper-server (default: whisper/whisper-server.exe, then PATH)")
    ap.add_argument("--whisper-model", default=None, help="path to the whisper ggml model (default: whisper/ggml-small.en.bin)")
    ap.add_argument("--whisper-url", default=None, help="use an already-running whisper-server at this URL instead of spawning one")
    args = ap.parse_args()

    base_url = args.url.rstrip("/")
    if not base_url.endswith("/v1"):
        base_url += "/v1"

    config = Config(
        url=base_url,
        model=args.model,
        system=args.system,
        temperature=args.temp,
        max_tokens=args.max_tokens,
        top_p=args.top_p,
        thinking_budget=args.thinking_budget,
        db_path=args.db,
        web=not args.no_web,
        memory=not args.no_memory,
        voice=not args.no_voice,
        whisper_bin=args.whisper_bin,
        whisper_model=args.whisper_model,
        whisper_url=args.whisper_url,
    )
    LlamaTUI(config).run()


if __name__ == "__main__":
    main()
