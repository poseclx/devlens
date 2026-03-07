# tests/conftest.py
import pytest
from dataclasses import dataclass, field


@pytest.fixture
def sample_pr_data():
    """Minimal PRData-like dict for testing without hitting GitHub API."""
    from devlens.github import PRData
    return PRData(
        number=42,
        title="Add user authentication",
        body="Implements JWT-based auth flow.",
        author="dev",
        base_branch="main",
        head_branch="feature/auth",
        additions=120,
        deletions=15,
        changed_files=5,
        labels=["feature"],
        files=[
            {
                "filename": "auth/jwt.py",
                "status": "added",
                "additions": 80,
                "deletions": 0,
                "patch": "+def verify_token(token):\n+    pass",
            },
            {
                "filename": "requirements.txt",
                "status": "modified",
                "additions": 2,
                "deletions": 0,
                "patch": "+PyJWT==2.8.0\n+cryptography==42.0.0",
            },
        ],
    )
