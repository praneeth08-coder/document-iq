"""DocumentIQ 2.0 - Centralized Scoring Engine.

Calculates a fair, explainable, and transparent risk score (0-100) using:
- 4 Risk Levels: Low (0-24), Moderate (25-59), High (60-84), Critical (85-100).
- Proportional severity weights: Critical (25 pts), High (18 pts), Medium (6 pts), Low (1 pt capped at 5).
- Density normalizer based on document clause volume.
- Safe ceiling capping for contracts with 0 High/Critical findings.
"""
from typing import Tuple, Dict, Any, List


def calculate_risk_score(critical: int, high: int, medium: int, low: int,
                         total_clauses: int = 0) -> Tuple[int, str, Dict[str, Any]]:
    """Calculate centralized risk score and level with explainable transparent breakdown."""
    if critical == 0 and high == 0 and medium == 0 and low == 0:
        return 0, "Low", {
            "base_points": 0,
            "density_multiplier": 1.0,
            "breakdown": "No liability risks detected. Contract contains standard provisions."
        }

    crit_points = critical * 25
    high_points = high * 18
    med_points = medium * 6
    low_points = min(5, low * 1)
    raw = crit_points + high_points + med_points + low_points

    # Density adjustment: normalize based on clause ratio
    density_mult = 1.0
    if total_clauses > 0:
        density = (critical * 3 + high * 2 + medium) / float(total_clauses)
        if (critical + high) <= 1 and density < 0.25:
            density_mult = 0.75
            raw = int(raw * density_mult)
        elif (critical + high) >= 3 and density > 0.50:
            density_mult = 1.20
            raw = int(raw * density_mult)

    # Risk Stratification:
    if critical == 0 and high == 0:
        # Balanced contracts without material unilateral liabilities capped strictly below Moderate threshold
        score = min(25, raw)
    elif critical == 0 and high == 1:
        # Moderate risk with 1 isolated high concern
        score = min(55, max(30, raw))
    elif critical == 0 and high == 2:
        # Elevated moderate risk
        score = min(75, max(58, raw))
    elif critical >= 1:
        # Contains Critical risks (e.g. unlimited indemnification, predatory acceleration)
        score = min(100, max(85, raw))
    else:
        # 3+ High risks
        score = min(100, max(80, raw))

    score = max(0, min(100, score))

    if score >= 85:
        level = "Critical"
    elif score >= 60:
        level = "High"
    elif score >= 25:
        level = "Moderate"
    else:
        level = "Low"

    explanation = (
        f"Score of {score}/100 ({level}) computed from {critical} Critical, {high} High, "
        f"{medium} Medium, and {low} Low findings across {total_clauses} clauses."
    )

    return score, level, {
        "raw_points": crit_points + high_points + med_points + low_points,
        "density_multiplier": density_mult,
        "explanation": explanation
    }
