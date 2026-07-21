"""
Specialist Agents — orchestrators for Job Finding and Longform Research tasks
using real web data extraction and LLM-powered analysis.
"""

from __future__ import annotations

import html as html_mod
import logging
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from uacc.config import config
from uacc.agent.controller import Agent
from uacc.models.base_adapter import BaseAdapter
from uacc.models.text_adapter import TextAdapter
from uacc.models.vision_adapter import VisionAdapter

logger = logging.getLogger(__name__)

_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def _fetch_text(url: str, timeout: int = 10) -> str:
    """Fetch a URL and return its text content."""
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        # Try common encodings
        for enc in ("utf-8", "iso-8859-1", "cp1252"):
            try:
                return raw.decode(enc)
            except (UnicodeDecodeError, UnicodeError):
                continue
        return raw.decode("utf-8", errors="replace")


def _extract_snippets(html: str, max_results: int = 10) -> List[Dict[str, str]]:
    """Extract search result snippets from DuckDuckGo HTML."""
    results = []
    # Match DDG result blocks
    pattern = r'<a[^>]*class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>'
    for link_match in re.finditer(pattern, html, re.DOTALL):
        url = link_match.group(1)
        title = re.sub(r"<[^>]+>", "", link_match.group(2)).strip()
        title = html_mod.unescape(title)
        results.append({"title": title, "url": url})
        if len(results) >= max_results:
            break
    return results


def _summarize_with_llm(
    text: str,
    instruction: str,
    adapter: Optional[BaseAdapter] = None,
) -> str:
    """Use the configured LLM to process text (summarize, extract, analyze).

    Falls back to returning the raw text if no LLM is available.
    """
    if adapter is None:
        return text

    prompt = f"{instruction}\n\n---\n{text[:6000]}"
    try:
        msgs = [
            {"role": "system", "content": "You are a research assistant. Respond with only the requested output, no extra commentary."},
            {"role": "user", "content": prompt},
        ]
        result = adapter._call_llm(msgs)
        return result.strip()
    except Exception as exc:
        logger.warning("LLM summarization failed: %s", exc)
        return text


# ── Job Search ────────────────────────────────────────────────


@dataclass
class JobListing:
    title: str = ""
    company: str = ""
    location: str = ""
    link: str = ""
    description: str = ""
    match_score: int = 0


class JobFinder:
    """Real job search using web data extraction and optional LLM enrichment."""

    def __init__(self, agent: Optional[Agent] = None):
        self.agent = agent
        self._adapter: Optional[BaseAdapter] = None
        if agent is not None:
            self._adapter = agent.adapter

    def _get_llm(self) -> Optional[BaseAdapter]:
        """Lazily create an LLM adapter for enrichment."""
        if self._adapter is not None:
            return self._adapter
        api_key = config.llm.openai_api_key or config.llm.anthropic_api_key
        if not api_key:
            return None
        try:
            model = config.llm.openai_model or config.llm.anthropic_model
            self._adapter = TextAdapter(model=model, api_key=api_key)
            return self._adapter
        except Exception:
            return None

    def run_search(self, title: str, location: str, remote: bool = True) -> Dict[str, Any]:
        search_query = f"{title} jobs in {location}"
        if remote:
            search_query += " remote"

        encoded = urllib.parse.quote(search_query)
        url = f"https://html.duckduckgo.com/html/?q={encoded}"

        logger.info("Fetching job search results: %s", url)
        try:
            html = _fetch_text(url, timeout=15)
        except Exception as exc:
            return {"success": False, "error": f"Failed to fetch search results: {exc}", "jobs": [], "report": ""}

        snippets = _extract_snippets(html, max_results=8)
        if not snippets:
            return {"success": False, "error": "No search results found", "jobs": [], "report": ""}

        jobs: List[JobListing] = []
        llm = self._get_llm()

        for s in snippets:
            text_block = f"Title: {s['title']}\nURL: {s['url']}"
            desc = text_block
            if llm:
                try:
                    desc = _summarize_with_llm(
                        text_block,
                        f"Extract a 1-sentence job description from this search result about '{title}'. If it's not a job posting, say 'Not a job posting.'",
                        llm,
                    )
                except Exception:
                    desc = text_block

            if "not a job posting" in desc.lower():
                continue

            job = JobListing(
                title=s["title"],
                company="",
                location=location,
                link=s["url"],
                description=desc,
                match_score=75,
            )
            jobs.append(job)

        report_lines = [
            f"# Job Search Report: {title}",
            f"- **Location**: {location}",
            f"- **Remote**: {remote}" if remote else "",
            f"- **Generated at**: {time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"- **Source**: DuckDuckGo search",
            "",
            "## Job Matches",
            "",
        ]

        for idx, job in enumerate(jobs, start=1):
            report_lines.append(f"### {idx}. {job.title}")
            report_lines.append(f"- **Link**: [{job.link}]({job.link})")
            report_lines.append(f"- **Description**: {job.description}\n")

        markdown_report = "\n".join(filter(None, report_lines))

        return {
            "success": True,
            "jobs_count": len(jobs),
            "jobs": [
                {"title": j.title, "company": j.company, "location": j.location, "link": j.link, "description": j.description, "match_score": j.match_score}
                for j in jobs
            ],
            "report": markdown_report,
            "message": f"Found {len(jobs)} job postings matching '{title}'",
            "search_url": url,
        }


# ── Deep Research ──────────────────────────────────────────────


class LongFormResearcher:
    """Real deep research using web search + content extraction + LLM synthesis."""

    def __init__(self, agent: Optional[Agent] = None):
        self.agent = agent
        self._adapter: Optional[BaseAdapter] = None
        if agent is not None:
            self._adapter = agent.adapter

    def _get_llm(self) -> Optional[BaseAdapter]:
        if self._adapter is not None:
            return self._adapter
        api_key = config.llm.openai_api_key or config.llm.anthropic_api_key
        if not api_key:
            return None
        try:
            model = config.llm.openai_model or config.llm.anthropic_model
            self._adapter = TextAdapter(model=model, api_key=api_key)
            return self._adapter
        except Exception:
            return None

    def run_research(self, topic: str, depth_levels: int = 3) -> Dict[str, Any]:
        """Perform real deep research by:
        1. Fetching search results from DuckDuckGo
        2. Extracting content from top result pages
        3. Synthesizing findings using the LLM
        """
        logger.info("Starting deep research on: %s (depth=%d)", topic, depth_levels)

        encoded = urllib.parse.quote(topic)
        search_url = f"https://html.duckduckgo.com/html/?q={encoded}"

        # Step 1: Fetch search results
        try:
            html = _fetch_text(search_url, timeout=15)
        except Exception as exc:
            return {"success": False, "error": f"Search failed: {exc}", "report": ""}

        snippets = _extract_snippets(html, max_results=6)
        if not snippets:
            return {"success": False, "error": "No search results found", "report": ""}

        # Step 2: Fetch content from top results
        sources = []
        for s in snippets[:depth_levels]:
            try:
                content = _fetch_text(s["url"], timeout=8)
                # Extract readable text (strip HTML tags)
                text = re.sub(r"<[^>]+>", " ", content)
                text = re.sub(r"\s+", " ", text).strip()[:3000]
                sources.append({"title": s["title"], "url": s["url"], "content": text})
            except Exception as exc:
                sources.append({"title": s["title"], "url": s["url"], "content": f"(Could not fetch: {exc})"})

        # Step 3: Synthesize with LLM
        llm = self._get_llm()

        if llm and sources:
            source_text = "\n\n".join(
                f"## Source: {s['title']}\nURL: {s['url']}\n{s['content'][:2000]}"
                for s in sources
            )
            synthesis_prompt = (
                f"You are a research analyst. Write a comprehensive, well-structured report on '{topic}' "
                f"based on the sources below. Include:\n"
                f"1. Executive Summary (2-3 paragraphs)\n"
                f"2. Key Findings (bullet points)\n"
                f"3. Detailed Analysis (break down into 3-4 subsections)\n"
                f"4. Sources Cited\n\n"
                f"Use a professional, analytical tone. Base everything on the provided sources."
            )
            report = _summarize_with_llm(source_text, synthesis_prompt, llm)
        else:
            # Fallback: build a structured report from source snippets
            lines = [f"# Research Report: {topic}", f"**Generated at**: {time.strftime('%Y-%m-%d %H:%M:%S')}", "", "## Sources", ""]
            for s in sources:
                lines.append(f"- [{s['title']}]({s['url']})")
                excerpt = s["content"][:500].strip()
                if excerpt:
                    lines.append(f"  > {excerpt}")
                    lines.append("")
            report = "\n".join(lines)

        return {
            "success": True,
            "topic": topic,
            "depth": depth_levels,
            "sources_count": len(sources),
            "report": report,
            "message": f"Research report on '{topic}' compiled from {len(sources)} sources",
            "search_url": search_url,
        }
