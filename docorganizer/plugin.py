"""Optional per-archive Python plugin.

When an archive directory contains ``docorganizer_plugin.py`` next to its
``docorganizer.yaml``, that module is auto-loaded and its hooks are called at
designated points in the pipeline. All hooks are optional — define only the
ones you need.

This keeps docorganizer's core generic. Vendor-specific knowledge (sender
canonicalization, invoice/receipt pair detection, supersedence heuristics
beyond what ``document_id`` covers) lives in each archive's plugin file.

Hooks
-----

``post_propose(proposal, text) -> Proposal``
    Called once per proposal, immediately after the LLM response has been
    converted into a Proposal and after ``apply_business_routing``, but before
    the three-document rule, supersedence detection, and validation. Use for
    single-proposal rewrites: canonicalize sender names, derive a
    ``disambiguator`` from extracted text, set ``document_id`` from a
    vendor-specific format, etc. Must return a Proposal (the same one,
    mutated in place, is fine).

``post_batch(proposals) -> list[Proposal]``
    Called once with the full proposal batch, after supersedence detection
    and before validation. Use for cross-document patterns: align
    disambiguators across invoice/receipt pairs, group same-vendor items, etc.
    Must return a list of proposals (typically the same list, possibly
    re-ordered).

Plugin file shape
-----------------

::

    # archive_root/docorganizer_plugin.py
    from docorganizer.cli import Proposal


    def post_propose(proposal: Proposal, text: str) -> Proposal:
        if proposal.sender == "Anthropic Ireland":
            proposal.sender = "Anthropic"
        return proposal


    def post_batch(proposals: list[Proposal]) -> list[Proposal]:
        # ... cross-document logic ...
        return proposals
"""

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional


PLUGIN_FILENAME = "docorganizer_plugin.py"


@dataclass
class Plugin:
    """Resolved hooks from an archive's plugin file. Missing hooks are None."""

    post_propose: Optional[Callable] = None
    post_batch: Optional[Callable] = None


_EMPTY = Plugin()


def load_plugin(archive_root: Path) -> Plugin:
    """Load ``docorganizer_plugin.py`` from ``archive_root`` if present.

    Returns an empty ``Plugin`` (all hooks None) when the file is absent or
    fails to import. Import errors are surfaced to stderr but never raised —
    a broken plugin must not block document processing.
    """
    plugin_path = archive_root / PLUGIN_FILENAME
    if not plugin_path.exists():
        return _EMPTY

    # Use a unique module name per plugin to avoid sys.modules pollution
    # across archives in the same Python process (e.g. test runs).
    module_name = f"docorganizer_user_plugin_{abs(hash(str(plugin_path)))}"

    try:
        spec = importlib.util.spec_from_file_location(module_name, plugin_path)
        if spec is None or spec.loader is None:
            return _EMPTY
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    except Exception as e:
        print(
            f"  Warning: failed to load {PLUGIN_FILENAME} from {archive_root}: {e}",
            file=sys.stderr,
        )
        return _EMPTY

    return Plugin(
        post_propose=getattr(module, "post_propose", None),
        post_batch=getattr(module, "post_batch", None),
    )
