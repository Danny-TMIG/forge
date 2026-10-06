import sys
import hashlib
import pytest
from pathlib import Path
from forge.dendritic import DendriticDiamondEngine, InvariantViolation

@pytest.fixture
def engine(tmp_path):
    """Provides a DendriticDiamondEngine instance isolated to a temporary directory."""
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True, exist_ok=True)
    mock_python = venv_bin / "python"
    mock_python.write_text("")

    eng = DendriticDiamondEngine(base_dir=tmp_path)
    return eng

def test_diamond_1_venv_isolation_success(monkeypatch, engine):
    """Verify Diamond 1 passes when execution is inside the .venv interpreter."""
    mock_python = engine.venv_path / "bin" / "python"
    monkeypatch.setattr(sys, "executable", str(mock_python))

    assert engine.diamond_1_ingress_gate(b"test-payload") is True

def test_diamond_1_venv_isolation_failure(monkeypatch, engine):
    """Verify Diamond 1 raises INVER-03 violation when outside .venv."""
    monkeypatch.setattr(sys, "executable", "/usr/bin/python3")

    with pytest.raises(InvariantViolation, match="INVER-03 Breach"):
        engine.diamond_1_ingress_gate(b"test-payload")

def test_diamond_2_mesh_lock_lifecycle(engine):
    """Verify Diamond 2 secures atomic locks and handles stale locks cleanly."""
    port = 8095
    service = "index-hub"

    # First acquisition
    assert engine.diamond_2_mesh_lock_gate(port, service) is True
    lock_file = engine.base_dir / f".lock_{service}_{port}"
    assert lock_file.exists()

    # Second acquisition (simulating a stale lock recovery)
    assert engine.diamond_2_mesh_lock_gate(port, service) is True
    assert lock_file.exists()

def test_dendritic_router_invalid_branch(engine):
    """Verify fan-out router catches unregistered branches."""
    with pytest.raises(ValueError, match="Unknown dendritic branch"):
        engine.execute_dendritic_router("quantum-hub", lambda: "data")

def test_diamond_3_seal_gate_verification(engine):
    """Verify Diamond 3 successfully validates correct checksums and rejects drift."""
    artifact = b"immutable-state-data"
    valid_checksum = hashlib.sha256(artifact).hexdigest()

    # Successful seal
    receipt = engine.diamond_3_seal_gate(artifact, valid_checksum)
    assert isinstance(receipt, str)
    assert len(receipt) == 64

    # Checksum mismatch violation (INVER-05)
    with pytest.raises(InvariantViolation, match="INVER-05 Breach"):
        engine.diamond_3_seal_gate(artifact, "corrupted_checksum_hash")
