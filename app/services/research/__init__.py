"""
Qwen Collective - Autonomous Web Research & Browsing Engine.
Provides secure public web search, content retrieval, headless browser automation,
evidence extraction, claim corroboration, deterministic citation auditing,
and report synthesis.
"""

from app.services.research.policy import ResearchSecurityPolicy, get_security_policy
from app.services.research.search import SearchManager
from app.services.research.retrieval import SecureFetcher
from app.services.research.browser import HeadlessBrowserService
from app.services.research.extraction import DocumentExtractor
from app.services.research.sources import SourceLibrary
from app.services.research.verification import EvidenceLedger
from app.services.research.citations import CitationEngine
from app.services.research.synthesis import ResearchSynthesizer
from app.services.research.manager import ResearchManager

__all__ = [
    "ResearchSecurityPolicy",
    "get_security_policy",
    "SearchManager",
    "SecureFetcher",
    "HeadlessBrowserService",
    "DocumentExtractor",
    "SourceLibrary",
    "EvidenceLedger",
    "CitationEngine",
    "ResearchSynthesizer",
    "ResearchManager",
]
