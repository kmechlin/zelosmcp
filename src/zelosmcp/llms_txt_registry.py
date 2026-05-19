"""Curated registry of known llms.txt URLs for popular frameworks.

Maps lowercase package/framework names to their published llms.txt
endpoints. Used as the default --urls list for MCPDoc config and as the
lookup table for Phase 2's auto-discovery resolver.
"""
from __future__ import annotations

KNOWN_LLMS_TXT: dict[str, str] = {
    "langgraph": "https://langchain-ai.github.io/langgraph/llms.txt",
    "langchain": "https://python.langchain.com/llms.txt",
    "fastapi": "https://fastapi.tiangolo.com/llms.txt",
    "react": "https://react.dev/llms.txt",
    "anthropic": "https://docs.anthropic.com/llms.txt",
    "pydantic": "https://docs.pydantic.dev/llms.txt",
    "pydantic-ai": "https://ai.pydantic.dev/llms.txt",
    "supabase": "https://supabase.com/llms.txt",
    "deno": "https://docs.deno.com/llms.txt",
    "nextjs": "https://nextjs.org/llms.txt",
    "tailwindcss": "https://tailwindcss.com/llms.txt",
    "modelcontextprotocol": "https://modelcontextprotocol.io/llms.txt",
    "crawl4ai": "https://docs.crawl4ai.com/llms.txt",
    "mintlify": "https://mintlify.com/llms.txt",
    "firecrawl": "https://docs.firecrawl.dev/llms.txt",
    "stripe": "https://docs.stripe.com/llms.txt",
    "langsmith": "https://docs.smith.langchain.com/llms.txt",
    "docker": "https://docs.docker.com/llms.txt",
}
