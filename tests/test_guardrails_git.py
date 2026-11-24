"""The experimental Git contract stays inert, even with opt-in."""
from unittest.mock import patch

import pytest

from git_sandbox import contract


@pytest.mark.parametrize("git_request", [
    {"operation": "status"}, {"operation": "branches"}, {"operation": "log", "limit": 10},
])
@pytest.mark.parametrize("opt_in", [False, True])
def test_valid_git_requests_never_start_a_process(git_request, opt_in):
    with patch("subprocess.Popen", side_effect=AssertionError("unexpected process")) as process:
        with pytest.raises(contract.GitUnavailable):
            contract.execute(git_request, opt_in=opt_in)
        process.assert_not_called()
