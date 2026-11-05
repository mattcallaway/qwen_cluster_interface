"""
Maintained Public Suffix List (PSL) Parser, Publisher Identity Resolution, and Content Deduplication.

Addresses Finding F-08 and Stage 4 Review Corrections:
1. Authoritative public suffix recognition for multipart ccSLDs (e.g. co.uk, com.au, co.jp)
   and private multi-tenant hosting platforms (e.g. github.io, pages.dev, substack.com).
2. Separate explicit source-publisher identity from registered-domain identity.
3. Prevent syndicated or commonly owned publications from counting as independent.
4. Robust content deduplication via 3-gram text shingling and lead-text similarity,
   while avoiding false deduplication of distinct stories with generic titles.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union
from urllib.parse import urlparse

from app.config import settings

logger = logging.getLogger("qwen_collective.research.public_suffix")

# Authoritative upstream source for Public Suffix List (maintained by Mozilla / ICANN community)
UPSTREAM_PSL_URL = "https://publicsuffix.org/list/public_suffix_list.dat"

# Dynamic / loaded PSL datasets (cached in memory)
_CUSTOM_EXACT_RULES: Dict[str, bool] = {}       # rule -> is_private
_CUSTOM_WILDCARD_RULES: Dict[str, bool] = {}    # base_domain -> is_private (e.g. *.tokyo.jp -> tokyo.jp)
_CUSTOM_EXCEPTION_RULES: Dict[str, bool] = {}   # full_domain -> is_private (e.g. !city.kawasaki.jp -> city.kawasaki.jp)

_PSL_DATASET_META: Dict[str, Any] = {
    "source": "bundled_snapshot",
    "upstream_url": UPSTREAM_PSL_URL,
    "last_updated": None,
    "sha256": None,
    "rules_count": 0,
    "is_bundled_fallback": True,
}

# Standard ICANN Country-Code Second-Level Domains (ccSLDs) and multi-part public suffixes
_ICANN_PUBLIC_SUFFIXES: Set[str] = {
    # United Kingdom
    "co.uk", "gov.uk", "ac.uk", "org.uk", "ltd.uk", "me.uk", "net.uk", "police.uk", "sch.uk",
    # Australia
    "com.au", "net.au", "org.au", "edu.au", "gov.au", "asn.au", "id.au",
    # Japan
    "co.jp", "ne.jp", "ac.jp", "go.jp", "or.jp", "ed.jp", "lg.jp",
    # New Zealand
    "co.nz", "net.nz", "org.nz", "govt.nz", "ac.nz", "geek.nz", "gen.nz", "school.nz",
    # Brazil
    "com.br", "net.br", "gov.br", "org.br", "edu.br", "adv.br", "blog.br",
    # India
    "co.in", "net.in", "org.in", "gen.in", "ac.in", "gov.in", "edu.in", "res.in", "nic.in",
    # Singapore
    "com.sg", "edu.sg", "gov.sg", "net.sg", "org.sg", "per.sg",
    # South Africa
    "co.za", "org.za", "gov.za", "ac.za", "edu.za", "net.za",
    # Mexico
    "com.mx", "gob.mx", "edu.mx", "org.mx", "net.mx",
    # Turkey
    "com.tr", "edu.tr", "gov.tr", "org.tr", "net.tr", "pol.tr",
    # South Korea
    "co.kr", "ne.kr", "re.kr", "go.kr", "or.kr", "ac.kr",
    # Taiwan
    "com.tw", "org.tw", "gov.tw", "edu.tw", "idv.tw",
    # China
    "com.cn", "net.cn", "gov.cn", "org.cn", "edu.cn",
    # Canada
    "gc.ca", "ab.ca", "bc.ca", "mb.ca", "nb.ca", "nl.ca", "ns.ca", "nt.ca", "nu.ca", "on.ca", "pe.ca", "qc.ca", "sk.ca", "yk.ca",
    # Israel
    "co.il", "org.il", "gov.il", "ac.il", "muni.il",
    # Hong Kong
    "com.hk", "org.hk", "gov.hk", "edu.hk", "net.hk",
    # Russia
    "com.ru", "net.ru", "org.ru",
}

# Private multi-tenant hosting platforms where tenant subdomains represent distinct entities
_PRIVATE_TENANT_SUFFIXES: Set[str] = {
    "github.io",
    "gitlab.io",
    "pages.dev",
    "webflow.io",
    "vercel.app",
    "netlify.app",
    "herokuapp.com",
    "firebaseapp.com",
    "appspot.com",
    "wordpress.com",
    "blogspot.com",
    "medium.com",
    "substack.com",
}

# Wire news services and syndication agencies (common source copy)
_WIRE_SERVICES: Dict[str, str] = {
    "apnews.com": "Associated Press",
    "ap.org": "Associated Press",
    "reuters.com": "Reuters",
    "afp.com": "Agence France-Presse",
    "bloomberg.com": "Bloomberg",
    "prnewswire.com": "PR Newswire",
    "businesswire.com": "Business Wire",
    "upi.com": "United Press International",
}

# Major media ownership groups (common corporate control)
_MEDIA_GROUPS: Dict[str, str] = {
    # News Corp / Fox
    "wsj.com": "News Corp",
    "nypost.com": "News Corp",
    "thetimes.co.uk": "News Corp",
    "thesun.co.uk": "News Corp",
    "theaustralian.com.au": "News Corp",
    "marketwatch.com": "News Corp",
    "barrons.com": "News Corp",
    # Gannett / USA Today Network
    "usatoday.com": "Gannett",
    "indystar.com": "Gannett",
    "freep.com": "Gannett",
    "azcentral.com": "Gannett",
    "cincinnati.com": "Gannett",
    "tennessean.com": "Gannett",
    # Hearst
    "chron.com": "Hearst",
    "sfgate.com": "Hearst",
    "timesunion.com": "Hearst",
    "seattlepi.com": "Hearst",
}

# Generic titles that must not trigger false duplicate detection
_GENERIC_TITLES: Set[str] = {
    "breaking news",
    "latest news",
    "latest updates",
    "daily roundup",
    "general news",
    "top stories",
    "news update",
    "home",
    "homepage",
    "index",
    "overview",
    "article",
    "news",
    "update",
    "monthly report",
    "weekly report",
}


def parse_psl_text(text: str) -> Tuple[Dict[str, bool], Dict[str, bool], Dict[str, bool], Dict[str, Any]]:
    """
    Parses authoritative Public Suffix List plain text format according to official specification:
    - Distinguishes ICANN DOMAINS section from PRIVATE DOMAINS section.
    - Captures exact rules (e.g. "co.uk", "github.io").
    - Captures wildcard rules (e.g. "*.tokyo.jp" -> base "tokyo.jp").
    - Captures exception rules (e.g. "!city.kawasaki.jp" -> "city.kawasaki.jp").
    """
    exact_rules: Dict[str, bool] = {}
    wildcard_rules: Dict[str, bool] = {}
    exception_rules: Dict[str, bool] = {}
    current_section: Optional[str] = None
    
    sha256_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("//"):
            if "===BEGIN ICANN DOMAINS===" in line:
                current_section = "ICANN"
            elif "===BEGIN PRIVATE DOMAINS===" in line:
                current_section = "PRIVATE"
            continue
        
        is_private = (current_section == "PRIVATE")
        rule = line.split()[0].lower()
        
        if rule.startswith("!"):
            # Exception rule: overrides wildcards
            exc_domain = rule[1:]
            if exc_domain:
                exception_rules[exc_domain] = is_private
        elif rule.startswith("*."):
            # Wildcard rule: matches any single label in wildcard position
            wc_base = rule[2:]
            if wc_base:
                wildcard_rules[wc_base] = is_private
        else:
            if rule and "." in rule:
                exact_rules[rule] = is_private
            
    meta = {
        "sha256": sha256_hash,
        "exact_rules": len(exact_rules),
        "wildcard_rules": len(wildcard_rules),
        "exception_rules": len(exception_rules),
        "total_rules": len(exact_rules) + len(wildcard_rules) + len(exception_rules),
        "parsed_at": datetime.now(timezone.utc).isoformat(),
    }
    return exact_rules, wildcard_rules, exception_rules, meta


def update_psl_dataset(
    source: Union[str, Path, bytes],
    expected_sha256: Optional[str] = None,
    save_to_disk: bool = True,
) -> Dict[str, Any]:
    """
    Updates the active Public Suffix List dataset from a file path, raw content, or URL.
    
    Guarantees:
    1. Verifies SHA-256 integrity if expected_sha256 is supplied.
    2. Uses SecureFetcher (PinningHTTPTransport) for remote URLs to prevent SSRF and DNS rebinding (F-14).
    3. Atomic memory swap of active suffix rules (exact, wildcard, exception).
    4. Fails closed / falls back to bundled snapshot if parsing or validation fails.
    5. Persists verified dataset to local data store when requested.
    """
    global _CUSTOM_EXACT_RULES, _CUSTOM_WILDCARD_RULES, _CUSTOM_EXCEPTION_RULES, _PSL_DATASET_META
    
    raw_text = ""
    source_desc = ""
    
    if isinstance(source, bytes):
        raw_text = source.decode("utf-8", errors="replace")
        source_desc = "bytes"
    elif isinstance(source, Path) or (isinstance(source, str) and not source.startswith("http")):
        p = Path(source)
        if not p.is_file():
            raise FileNotFoundError(f"PSL dataset file not found: {p}")
        raw_text = p.read_text(encoding="utf-8", errors="replace")
        source_desc = str(p)
    elif isinstance(source, str) and source.startswith("http"):
        # Remote fetch using SSRF-resistant retrieval mechanism (PinningHTTPTransport)
        from app.services.research.retrieval import SecureFetcher
        source_desc = source
        fetcher = SecureFetcher()
        fetch_res = fetcher.fetch(source, timeout_seconds=15.0, max_bytes=5 * 1024 * 1024)
        if not fetch_res.is_success or not fetch_res.content_bytes:
            logger.warning(
                "Remote PSL dataset fetch failed (%s): %s; maintaining bundled fallback",
                source,
                fetch_res.error,
            )
            return {
                "status": "failed_fallback_retained",
                "error": fetch_res.error,
                "is_bundled_fallback": True,
                "current_meta": _PSL_DATASET_META,
            }
        raw_text = fetch_res.content_bytes.decode("utf-8", errors="replace")
    else:
        raise ValueError(f"Unsupported PSL source type: {type(source)}")
        
    actual_sha256 = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
    if expected_sha256 and actual_sha256.lower() != expected_sha256.lower():
        raise ValueError(f"PSL SHA-256 mismatch: expected {expected_sha256}, got {actual_sha256}")
        
    exact_r, wildcard_r, exc_r, meta = parse_psl_text(raw_text)
    if not exact_r and not wildcard_r and not exc_r:
        raise ValueError("PSL parsing yielded 0 rules; rejected corrupted dataset")
        
    # Atomic memory update
    _CUSTOM_EXACT_RULES = exact_r
    _CUSTOM_WILDCARD_RULES = wildcard_r
    _CUSTOM_EXCEPTION_RULES = exc_r
    
    _PSL_DATASET_META = {
        "source": source_desc,
        "upstream_url": UPSTREAM_PSL_URL,
        "last_updated": meta["parsed_at"],
        "sha256": actual_sha256,
        "rules_count": meta["total_rules"],
        "exact_rules_count": meta["exact_rules"],
        "wildcard_rules_count": meta["wildcard_rules"],
        "exception_rules_count": meta["exception_rules"],
        "is_bundled_fallback": False,
    }
    
    if save_to_disk:
        try:
            target_dir = Path(settings.data_dir) / "psl"
            target_dir.mkdir(parents=True, exist_ok=True)
            target_file = target_dir / "public_suffix_list.dat"
            target_file.write_text(raw_text, encoding="utf-8")
            _PSL_DATASET_META["stored_path"] = str(target_file)
        except Exception as ex:
            logger.warning("Failed saving PSL snapshot to disk: %s", ex)
            
    logger.info("PSL dataset updated successfully: %d rules (SHA-256 %s)", meta["total_rules"], actual_sha256[:12])
    return {
        "status": "success",
        "metadata": _PSL_DATASET_META,
    }


def get_psl_dataset_info() -> Dict[str, Any]:
    """Returns metadata about the active Public Suffix List dataset and its maintenance state."""
    custom_count = len(_CUSTOM_EXACT_RULES) + len(_CUSTOM_WILDCARD_RULES) + len(_CUSTOM_EXCEPTION_RULES)
    fallback_count = len(_ICANN_PUBLIC_SUFFIXES) + len(_PRIVATE_TENANT_SUFFIXES)
    return {
        "active_source": _PSL_DATASET_META.get("source"),
        "upstream_canonical_url": UPSTREAM_PSL_URL,
        "is_bundled_fallback": _PSL_DATASET_META.get("is_bundled_fallback", True),
        "sha256": _PSL_DATASET_META.get("sha256"),
        "last_updated": _PSL_DATASET_META.get("last_updated"),
        "effective_rules_count": custom_count if custom_count > 0 else fallback_count,
        "effective_icann_count": len([r for r, priv in _CUSTOM_EXACT_RULES.items() if not priv]) if custom_count > 0 else len(_ICANN_PUBLIC_SUFFIXES),
        "effective_private_count": len([r for r, priv in _CUSTOM_EXACT_RULES.items() if priv]) if custom_count > 0 else len(_PRIVATE_TENANT_SUFFIXES),
        "wildcard_rules_count": len(_CUSTOM_WILDCARD_RULES),
        "exception_rules_count": len(_CUSTOM_EXCEPTION_RULES),
    }


def parse_registered_domain(url_or_host: str) -> Tuple[str, str, bool]:
    """
    Parses a URL or hostname into (registered_domain, public_suffix, is_private_tenant)
    strictly implementing Mozilla Public Suffix List specification:
    1. Exception rules (!rule) take precedence over wildcards and exact rules.
       If an exception matches, the public suffix is the rule minus its leftmost label.
    2. Longest matching wildcard (*.base) or exact rule prevails.
       For a wildcard rule (*.base), the public suffix includes the wildcard label itself.
    3. Bundled standard ccSLDs and private tenant suffixes are applied as fallbacks.
    """
    if not url_or_host:
        return ("", "", False)
    
    # Strip scheme and path if full URL
    host = url_or_host.strip().lower()
    if "://" in host or "/" in host:
        parsed = urlparse(url_or_host if "://" in url_or_host else f"http://{url_or_host}")
        host = (parsed.hostname or host).lower()
    
    if ":" in host:
        host = host.split(":")[0]
    
    parts = host.split(".")
    if len(parts) <= 1:
        return (host, "", False)

    # 1. Check exception rules (!rule) - highest precedence
    for i in range(len(parts)):
        cand = ".".join(parts[i:])
        if cand in _CUSTOM_EXCEPTION_RULES:
            is_priv = _CUSTOM_EXCEPTION_RULES[cand]
            # Public suffix is the rule minus its leftmost label
            suffix = ".".join(parts[i + 1:]) if i + 1 < len(parts) else ""
            # Registered domain is the exception rule match itself
            reg_domain = cand
            return (reg_domain, suffix, is_priv)

    # 2. Find longest matching wildcard or exact rule
    best_suffix = ""
    best_is_priv = False
    max_rule_labels = 0

    # A. Exact rules (custom + bundled)
    for i in range(len(parts)):
        cand = ".".join(parts[i:])
        is_priv = False
        matched = False
        if cand in _CUSTOM_EXACT_RULES:
            is_priv = _CUSTOM_EXACT_RULES[cand]
            matched = True
        elif cand in _PRIVATE_TENANT_SUFFIXES:
            is_priv = True
            matched = True
        elif cand in _ICANN_PUBLIC_SUFFIXES:
            is_priv = False
            matched = True

        if matched:
            rule_labels = len(parts) - i
            if rule_labels > max_rule_labels:
                max_rule_labels = rule_labels
                best_suffix = cand
                best_is_priv = is_priv

    # B. Wildcard rules (*.base)
    # If base is tokyo.jp, and host has bar.tokyo.jp (i is index of bar)
    for i in range(len(parts) - 1):
        base_cand = ".".join(parts[i + 1:])
        if base_cand in _CUSTOM_WILDCARD_RULES:
            is_priv = _CUSTOM_WILDCARD_RULES[base_cand]
            rule_labels = len(parts) - (i + 1) + 1  # base labels + 1 for wildcard
            if rule_labels > max_rule_labels:
                max_rule_labels = rule_labels
                # Public suffix includes the wildcard label parts[i]
                best_suffix = ".".join(parts[i:])
                best_is_priv = is_priv

    if best_suffix:
        # Determine registered domain (suffix + 1 preceding label)
        suffix_parts = best_suffix.split(".")
        suffix_len = len(suffix_parts)
        if len(parts) > suffix_len:
            reg_domain = ".".join(parts[-(suffix_len + 1):])
            return (reg_domain, best_suffix, best_is_priv)
        return (host, best_suffix, best_is_priv)

    # 3. Fallback: single TLD (e.g. example.com -> ("example.com", "com", False))
    reg_domain = ".".join(parts[-2:])
    return (reg_domain, parts[-1], False)


def resolve_publisher_identity(url: str, declared_publisher: Optional[str] = None) -> str:
    """
    Determines the authoritative publisher identity, keeping explicit publisher identity
    separate from registered-domain identity.
    
    Normalizes wire syndication (e.g. AP, Reuters) and common media ownership networks
    so syndicated copy is not treated as independent corroboration.
    """
    reg_domain, _, is_tenant = parse_registered_domain(url)
    
    # Check if declared publisher mentions wire services
    if declared_publisher:
        pub_lower = declared_publisher.strip().lower()
        if any(wire in pub_lower for wire in ("associated press", "ap news", " ap ")):
            return "wire:associated_press"
        if "reuters" in pub_lower:
            return "wire:reuters"
        if "agence france-presse" in pub_lower or "afp" in pub_lower:
            return "wire:afp"
        if "bloomberg" in pub_lower:
            return "wire:bloomberg"
        if "pr newswire" in pub_lower:
            return "wire:pr_newswire"
        if "business wire" in pub_lower:
            return "wire:business_wire"

    # Check known domain wire services
    if reg_domain in _WIRE_SERVICES:
        return f"wire:{_WIRE_SERVICES[reg_domain].lower().replace(' ', '_')}"

    # Check known media ownership groups
    if reg_domain in _MEDIA_GROUPS:
        return f"conglomerate:{_MEDIA_GROUPS[reg_domain].lower().replace(' ', '_')}"

    # If tenant subdomain, the tenant is the publisher
    if is_tenant:
        return f"tenant:{reg_domain}"

    # Fall back to declared publisher if provided, else registered domain
    if declared_publisher and len(declared_publisher.strip()) > 2:
        return f"publisher:{declared_publisher.strip().lower()}"

    return f"domain:{reg_domain}"


def are_sources_independent(
    url_a: str,
    url_b: str,
    publisher_a: Optional[str] = None,
    publisher_b: Optional[str] = None,
) -> bool:
    """
    Determines whether two sources are genuinely independent:
    - Must have distinct registered domains / tenant spaces
    - Must not share the same underlying publisher, wire syndicate, or media group
    """
    domain_a, _, _ = parse_registered_domain(url_a)
    domain_b, _, _ = parse_registered_domain(url_b)
    
    if domain_a == domain_b:
        return False

    pub_id_a = resolve_publisher_identity(url_a, publisher_a)
    pub_id_b = resolve_publisher_identity(url_b, publisher_b)
    
    if pub_id_a == pub_id_b:
        return False

    return True


def compute_text_shingles(text: str, k: int = 3) -> Set[str]:
    """Extract word k-grams (shingles) for similarity estimation."""
    words = [w.lower() for w in re.findall(r"\b[a-zA-Z0-9]{3,}\b", text)]
    if len(words) < k:
        return set(words)
    return {" ".join(words[i : i + k]) for i in range(len(words) - k + 1)}


def jaccard_similarity(set_a: Set[str], set_b: Set[str]) -> float:
    """Calculate Jaccard similarity between two shingle sets."""
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a.intersection(set_b))
    union = len(set_a.union(set_b))
    return intersection / union if union > 0 else 0.0


def is_duplicate_or_syndicated(
    src_a: Dict[str, Any],
    src_b: Dict[str, Any],
    shingle_threshold: float = 0.55,
) -> Tuple[bool, str]:
    """
    Determines whether two source records are duplicates or syndicated copies of each other.
    
    Combines:
    1. Exact full-content hash comparison.
    2. Article title comparison (excluding generic titles).
    3. Multi-factor text shingle similarity (detecting syndicated stories with modified headlines).
    4. Lead paragraph / summary text overlap.
    """
    hash_a = src_a.get("content_hash") or ""
    hash_b = src_b.get("content_hash") or ""
    if hash_a and hash_b and hash_a == hash_b:
        return (True, "identical_content_hash")

    title_a = (src_a.get("title") or "").strip().lower()
    title_b = (src_b.get("title") or "").strip().lower()
    
    text_a = src_a.get("extracted_text") or src_a.get("clean_text") or ""
    text_b = src_b.get("extracted_text") or src_b.get("clean_text") or ""

    # Check exact title match, avoiding generic titles
    if title_a and title_b and title_a == title_b:
        if title_a not in _GENERIC_TITLES and len(title_a) >= 20:
            # If title is specific and matches, check that content isn't totally unrelated
            shingles_a = compute_text_shingles(text_a[:2000], k=3)
            shingles_b = compute_text_shingles(text_b[:2000], k=3)
            sim = jaccard_similarity(shingles_a, shingles_b)
            if sim >= 0.20 or not text_a or not text_b:
                return (True, "identical_specific_title")

    # Check text shingling for syndicated stories with modified titles
    if text_a and text_b:
        # Check first 1500 characters / lead paragraph
        lead_a = text_a[:1500]
        lead_b = text_b[:1500]
        shingles_a = compute_text_shingles(lead_a, k=3)
        shingles_b = compute_text_shingles(lead_b, k=3)
        sim = jaccard_similarity(shingles_a, shingles_b)
        if sim >= shingle_threshold:
            return (True, f"syndicated_content_similarity_{sim:.2f}")

    return (False, "distinct_sources")
