"""Pure offline current-source selection for *one independently audited run*.

Current source selection is not current calibration. A multi-point series must
use one measurement method and the same original evidence SHA-256 throughout.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Sequence

from audit_r4_5_powered_step_evidence import AuditError

# Source names describe the instrument *and* the physical measurement method.
PSU_READBACK = 'PSU_READBACK'
DMM_SERIES_DCI = 'DMM_SERIES_DCI'
DIRECT_BALLAST_DCV_OVER_R = 'DIRECT_BALLAST_DCV_OVER_R'
SEQUENTIAL_DIODE_DCV_OVER_R = 'SEQUENTIAL_PSU_MINUS_DIODE_DCV_OVER_R'
ALLOWED_METHODS = frozenset((PSU_READBACK, DMM_SERIES_DCI,
                             DIRECT_BALLAST_DCV_OVER_R,
                             SEQUENTIAL_DIODE_DCV_OVER_R))
AUTO_ORDER = (DMM_SERIES_DCI, DIRECT_BALLAST_DCV_OVER_R,
              SEQUENTIAL_DIODE_DCV_OVER_R)


@dataclass(frozen=True)
class CurrentCandidate:
    run_sha256: str
    index: int
    method: str
    current_ma: float
    role: str
    sampling: str
    qualification: str


def _require(predicate: bool, message: str):
    if not predicate:
        raise AuditError(message)


def select_current_series(point_candidates: Sequence[Sequence[CurrentCandidate]], *,
                          source: str = 'auto', intent: str = 'diagnostic_screening',
                          independently_audited: bool = False):
    """Return (selected candidates, metadata), or fail without a fallback.

    The caller MUST run an independent original-JSONL audit first. This function
    enforces selection invariants, but cannot itself establish wire provenance.
    """
    _require(independently_audited is True, 'Independent original-evidence audit required')
    _require(intent == 'diagnostic_screening',
             'Calibrated or metrological I-V claim is not authorized')
    _require(source == 'auto' or source in ALLOWED_METHODS,
             'Unknown requested current source')
    _require(source != PSU_READBACK,
             'PSU readback is control telemetry, not an I-V current source')
    _require(isinstance(point_candidates, (tuple, list)) and
             1 <= len(point_candidates) <= 1000, 'Missing or unbounded point series')
    per_point: list[dict[str, CurrentCandidate]] = []
    evidence_sha = None
    for idx, choices in enumerate(point_candidates):
        _require(isinstance(choices, (tuple, list)) and 1 <= len(choices) <= 8,
                 'Invalid number of candidates at point')
        offered = {}
        for c in choices:
            _require(type(c) is CurrentCandidate, 'Current candidate has untrusted type')
            _require(isinstance(c.run_sha256, str) and
                     re.fullmatch(r'[0-9a-f]{64}', c.run_sha256) is not None,
                     'Current candidate has invalid original evidence fingerprint')
            _require(type(c.index) is int and c.index == idx,
                     'Point identity mismatch or reordered source samples')
            _require(c.method in ALLOWED_METHODS and c.method not in offered,
                     'Unsupported or duplicated current method at one point')
            _require(type(c.current_ma) in (int, float) and
                     math.isfinite(c.current_ma) and 0 < c.current_ma < 100_000,
                     'Invalid, nonpositive, or non-finite current')
            _require(c.qualification == 'UNQUALIFIED',
                     'Unsupported claim of calibrated current accuracy')
            _require(c.sampling == 'SEQUENTIAL_NOT_SYNCHRONIZED',
                     'Unsupported sampling/synchronization assertion')
            _require(c.role == ('PSU_CONTROL_TELEMETRY' if c.method == PSU_READBACK
                                else 'DIAGNOSTIC_SCREENING'),
                     'Source role and measurement method disagree')
            if evidence_sha is None:
                evidence_sha = c.run_sha256
            _require(c.run_sha256 == evidence_sha,
                     'Cannot combine different original runs into one curve')
            offered[c.method] = c
        per_point.append(offered)
    # Determine the complete-series intersection, never choose a different
    # sensor on a per-point basis and never invent a DMM reading from another run.
    common = set(per_point[0])
    for offered in per_point[1:]:
        common.intersection_update(offered)
    common.discard(PSU_READBACK)
    if source == 'auto':
        chosen_method = next((method for method in AUTO_ORDER if method in common), None)
    else:
        chosen_method = source if source in common else None
    _require(chosen_method is not None,
             'Requested current source unavailable at every point of this run; no fallback')
    selected = [offered[chosen_method] for offered in per_point]
    return selected, {
        'status': 'AUDITED_DIAGNOSTIC_CURRENT_SOURCE_SELECTED',
        'run_sha256': evidence_sha,
        'selection': chosen_method,
        'requested_source': source,
        'selection_is_calibration': False,
        'metrology_qualification': 'UNQUALIFIED',
        'intent': intent,
        'point_count': len(selected),
        'sample_timing': 'SEQUENTIAL_NOT_SYNCHRONIZED',
        'psu_readback_is_reference': False,
        'cross_run_mixing_allowed': False,
        'per_point_source_fallback_allowed': False,
    }
