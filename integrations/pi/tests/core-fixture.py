"""Offline Jev doubles behind the real Foreman process protocol."""
from foreman import cli
from foreman.models import ForemanResult
from foreman.routing import RoutingDecision


class Model:
    def __init__(self, **kwargs):
        pass

    async def assess(self, observation, checks):
        scores = {}
        for check in checks:
            scores.setdefault(check.responsibility_id, {})[check.check_id] = 0.99
        return ForemanResult(checks=scores)

    async def close(self):
        pass


class Router:
    def __init__(self, **kwargs):
        pass

    async def route(self, work, candidates):
        return RoutingDecision(active_responsibility_ids=list(candidates.global_ids()))

    async def close(self):
        pass


cli.JevForemanModel = Model
cli.JevResponsibilityRouter = Router
cli.app()
