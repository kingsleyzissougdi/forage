from pathlib import Path

from forage.flow import run_demo


def test_demo_proves_core_invariants(tmp_path: Path):
    result = run_demo(tmp_path / "demo.sqlite")
    assert result["blocked_forbidden"] is True
    assert result["fabricated_rejected"] is True
    assert result["overspend_blocked"] is True
    assert result["hostile_redacted"] is True
    assert result["post_replay"] is True
    assert result["decision"] == "FAILED"
    assert result["restored_status"] == "FAILED"
    assert result["policy_dry_run"] is True
