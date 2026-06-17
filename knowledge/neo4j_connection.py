"""
Neo4j connection singleton.

Reads NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD from environment (or .env).
Returns None from get_driver() when Neo4j is unreachable so callers can
fall back to NetworkX without crashing.
"""
import logging
import os

log = logging.getLogger(__name__)

# Load .env if present (no hard dependency — skip gracefully if python-dotenv absent)
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"),
        override=False,
    )
except ImportError:
    pass

_driver = None          # module-level singleton
_available: bool = None # None = not yet probed


def get_driver():
    """Return a live Neo4j driver, or None if unavailable."""
    global _driver, _available
    if _available is False:
        return None
    if _driver is not None:
        return _driver
    # Read credentials lazily so Streamlit secrets are loaded first
    uri      = os.environ.get("NEO4J_URI",      "bolt://localhost:7687")
    user     = os.environ.get("NEO4J_USER",     "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "")
    if not password:
        log.info("NEO4J_PASSWORD not set — skipping Neo4j, using NetworkX fallback")
        _available = False
        return None
    try:
        from neo4j import GraphDatabase
        drv = GraphDatabase.driver(uri, auth=(user, password))
        drv.verify_connectivity()
        _driver    = drv
        _available = True
        log.info("Neo4j connected at %s", uri)
        return _driver
    except Exception as exc:
        log.warning("Neo4j unavailable (%s) — falling back to NetworkX", exc)
        _available = False
        return None


def is_available() -> bool:
    """Return True iff Neo4j is reachable (probes on first call)."""
    return get_driver() is not None


def close():
    """Close the driver (call on app shutdown if desired)."""
    global _driver, _available
    if _driver is not None:
        _driver.close()
        _driver    = None
        _available = None
