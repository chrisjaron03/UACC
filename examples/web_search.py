"""
Demo: Web Search — full agent demo that opens a browser and searches the web.

Usage:
    set OPENAI_API_KEY=sk-...
    python examples/web_search.py "What is UACC?"
    python examples/web_search.py --mode text "Python release date"
"""

import argparse
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)


def main():
    parser = argparse.ArgumentParser(description="UACC — Web Search Demo")
    parser.add_argument(
        "query",
        type=str,
        help="What to search for",
    )
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
        "--browser",
        type=str,
        default="chrome",
        choices=["chrome", "firefox", "edge"],
        help="Browser to use (default: chrome)",
    )
    parser.add_argument(
        "--max-iter",
        type=int,
        default=20,
        help="Max iterations (default: 20)",
    )
    args = parser.parse_args()

    from uacc.agent.controller import Agent

    agent = Agent(
        mode=args.mode,
        model=args.model,
        max_iterations=args.max_iter,
        human_mimicry=True,
        safe_mode=True,
    )

    task = (
        f"Open a web browser ({args.browser}) and search Google for: '{args.query}'. "
        f"Steps: "
        f"1. Press Win+R to open Run dialog "
        f"2. Type '{args.browser}' and press Enter to open the browser "
        f"3. Wait for the browser to load "
        f"4. Click on the address bar (or press Ctrl+L) "
        f"5. Type 'https://www.google.com' and press Enter "
        f"6. Wait for Google to load "
        f"7. Click on the search box "
        f"8. Type '{args.query}' and press Enter "
        f"9. Wait for search results to load "
        f"10. Report 'done' with a brief summary of what you see on the results page"
    )

    print("\n🌐 Starting UACC Web Search Agent")
    print(f"   Query: {args.query}")
    print(f"   Browser: {args.browser}")
    print(f"   Mode: {args.mode}")
    print()

    result = agent.run(task)

    print(f"\n{'='*60}")
    print("  RESULT")
    print(f"{'='*60}")
    print(f"  Success: {result['success']}")
    print(f"  Message: {result['message']}")
    print(f"  Iterations: {result['iterations']}")
    summary = result.get("summary", {})
    print(f"  Total actions: {summary.get('total_actions', '?')}")
    print(f"  Elapsed: {summary.get('elapsed_seconds', '?')}s")


if __name__ == "__main__":
    main()
