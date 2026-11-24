"""Experimental Git request contract; deliberately never executes Git.

Not connected to Caio's registry. The opt-in flag alone is not a security
boundary. There is no verified worker, so even a valid opt-in remains blocked.
"""
from __future__ import annotations


class InvalidRequest(ValueError):
    pass


class GitUnavailable(RuntimeError):
    pass


def validate_request(request: object) -> tuple[str, int | None]:
    """Accept only predefined read operations; no path, refs, flags or shell.

    This is a wire contract experiment, not an authorization mechanism.
    """
    if type(request) is not dict:
        raise InvalidRequest('request must be an object')
    if set(request) - {'operation', 'limit'} or 'operation' not in request:
        raise InvalidRequest('unsupported request fields')
    operation = request['operation']
    if type(operation) is not str or operation not in ('status', 'branches', 'log'):
        raise InvalidRequest('unsupported operation')
    limit = request.get('limit')
    if operation != 'log' and 'limit' in request:
        raise InvalidRequest('limit only applies to log')
    if operation == 'log':
        if limit is None and 'limit' not in request:
            limit = 20
        if type(limit) is not int or not 1 <= limit <= 50:
            raise InvalidRequest('log limit must be 1..50')
    return operation, limit


def execute(request: object, *, opt_in: bool = False) -> str:
    """Fail closed regardless of opt-in until a separately verified worker exists."""
    validate_request(request)
    if opt_in is not True:
        raise GitUnavailable('Git local desativado: requer opt-in administrativo')
    raise GitUnavailable('Git local indisponível: isolamento integral não verificado')
