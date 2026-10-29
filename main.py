import argparse
import os
import sys

from graph import build_graph


def main():
    parser = argparse.ArgumentParser(
        description="Paper Research Agent: a local literature research workflow"
    )
    parser.add_argument(
        "--keywords",
        type=str,
        nargs="+",
        required=True,
        help="Search keywords",
    )
    args = parser.parse_args()

    keywords = args.keywords
    print(f"\n🔬 Starting research workflow for: {', '.join(keywords)}\n")
    print("=" * 60)

    try:
        # Build and run the graph
        graph = build_graph()
        result = graph.invoke({"keywords": keywords})

        print("\n" + "=" * 60)
        print("✅ Research workflow completed!")
        print("✅ 文献调研完成！")

        return result
    except ValueError as e:
        print(f"\n❌ Error: {e}")
        print("\nCheck your LLM configuration in .env (see document/README.md).")
        if os.getenv("LLM_PROVIDER", "openai").strip().lower() == "codex":
            print("For Codex, run 'codex login status' and check CODEX_CLI_PATH.")
        else:
            print("For OpenAI-compatible APIs, set OPENAI_API_KEY and OPENAI_MODEL.")
        sys.exit(1)
    except Exception as e:
        error_msg = str(e)
        if "401" in error_msg or "api_key" in error_msg.lower() or "Incorrect API key" in error_msg:
            print(f"\n❌ Authentication error: {e}")
            if os.getenv("LLM_PROVIDER", "openai").strip().lower() == "codex":
                print("\nCheck 'codex login status'; run 'codex login' if needed.")
            else:
                print("\nPlease check your OPENAI_API_KEY in the .env file or environment.")
        else:
            print(f"\n❌ Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
