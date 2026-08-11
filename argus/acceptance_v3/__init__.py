"""Pure, guarded evidence tooling for the superseding Argus acceptance v3 run.

The package deliberately owns no HTTP client, provider, database, or deployment
authority.  Callers inject those adapters and hand the package bounded,
redacted observations to validate and persist.
"""

from .bundle import (
    EIGHT_GATES,
    RUBRIC_CELLS,
    BundleError,
    calculate_score,
    derive_verdict,
    evaluate_gates,
    terminal_sections,
    verify_bundle,
    write_bundle,
)
from .contract import (
    CYCLE_ID,
    GLOBAL_GUARD_PATH,
    PROFILE,
    SCHEMA,
    ContractError,
    bind_returned_run,
    build_execution_contract,
    canonical_bytes,
    canonical_hash,
    create_evidence_root,
    create_global_guard,
    write_immutable_json,
)

__all__ = (
    "BundleError",
    "ContractError",
    "CYCLE_ID",
    "EIGHT_GATES",
    "GLOBAL_GUARD_PATH",
    "PROFILE",
    "RUBRIC_CELLS",
    "SCHEMA",
    "bind_returned_run",
    "build_execution_contract",
    "calculate_score",
    "derive_verdict",
    "canonical_bytes",
    "canonical_hash",
    "create_evidence_root",
    "create_global_guard",
    "evaluate_gates",
    "terminal_sections",
    "verify_bundle",
    "write_bundle",
    "write_immutable_json",
)
