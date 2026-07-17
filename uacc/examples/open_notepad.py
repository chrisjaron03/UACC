"""
Demo: Open Notepad — full agent demo that opens Notepad, types text, and saves.

This is the simplest end-to-end agent demo. It requires an LLM API key.

Usage:
    # With OpenAI
    set OPENAI_API_KEY=sk-...
    python examples/open_notepad.py

    # With local Ollama
    set OPENAI_BASE_URL=http://localhost:11434/v1
    set LOCAL_MODEL=llama3.1:70b
    python examples/open_notepad.py --mode text

    # Hybrid mode (default, requires vision model)
    python examples/open_notepad.py --mode hybrid
"""

import argparse
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)


def main():
    parser = argparse.ArgumentParser(description="UACC — Open Notepad Demo")
    parser.add_argument(
        "--mode",
        choices=["text", "vision", "hybrid"],
        default="hybrid",
        help="Agent mode (default: hybrid)",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Override LLM model name",
    )
    parser.add_argument(
        "--no-mimicry",
        action="store_true",
        help="Disable human-like mouse movement",
    )
    parser.add_argument(
        "--max-iter",
        type=int,
        default=15,
        help="Max iterations (default: 15)",
    )
    args = parser.parse_args()

    from uacc.agent.controller import Agent

    agent = Agent(
        mode=args.mode,
        model=args.model,
        max_iterations=args.max_iter,
        human_mimicry=not args.no_mimicry,
        safe_mode=True,
    )

    task = (
        "Open Notepad by pressing Win+R, typing 'notepad', and pressing Enter. "
        "Then type the following text: 'Hello from UACC! 🖥️\\n"
        "This text was typed by an AI agent controlling the computer.\\n"
        "The current date is shown in the title bar.'"
        "After typing, save the file as 'uacc_test.txt' on the Desktop using Ctrl+S."
    )

    print(f"\n🚀 Starting UACC Agent in {args.mode} mode")
    print(f"   Model: {args.model or 'default'}")
    print(f"   Human mimicry: {not args.no_mimicry}")
    print()

    result = agent.run(task)

    print(f"\n{'='*60}")
    print(f"  RESULT")
    print(f"{'='*60}")
    print(f"  Success: {result['success']}")
    print(f"  Message: {result['message']}")
    print(f"  Iterations: {result['iterations']}")
    summary = result.get("summary", {})
    print(f"  Total actions: {summary.get('total_actions', '?')}")
    print(f"  Success rate: {summary.get('success_rate', '?')}")
    print(f"  Elapsed: {summary.get('elapsed_seconds', '?')}s")


if __name__ == "__main__":
    main()
