"""List the model ids available to OPENAI_API_KEY.

Carbon R does not hard-code a model id. Provider model names change often, and a name
copied from a blog post is an unverified value — exactly what this project's rules forbid.
Run this against your own key, then set OPENAI_MODEL in .env to something that is really
there.

    uv run python scripts/list_models.py
"""

import sys

from openai import OpenAI, OpenAIError

from carbon_r.config import get_settings


def main() -> int:
    settings = get_settings()
    if not settings.openai_api_key:
        print("OPENAI_API_KEY is not set in .env", file=sys.stderr)
        return 1

    try:
        models = OpenAI(api_key=settings.openai_api_key).models.list()
    except OpenAIError as exc:
        print(f"Could not list models: {exc}", file=sys.stderr)
        return 1

    ids = sorted(m.id for m in models.data)
    print(f"{len(ids)} models available to this key.\n")
    print("Chat/vision candidates:")
    for mid in ids:
        if any(k in mid for k in ("gpt", "o1", "o3", "o4", "chatgpt")):
            print(f"  {mid}")
    print("\nEverything else:")
    for mid in ids:
        if not any(k in mid for k in ("gpt", "o1", "o3", "o4", "chatgpt")):
            print(f"  {mid}")
    print("\nSet the chosen id as OPENAI_MODEL in .env.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
