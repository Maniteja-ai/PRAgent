from pathlib import Path

import pytest

from impact_agent.config.loader.implementations.json_config_loader import JsonConfigLoader
from impact_agent.dependencies.agent_factory import AgentFactory

CONFIG = Path(__file__).parents[1] / "config" / "default"


class Dependencies:
    def receive(self, headers, body):
        raise NotImplementedError

    def submit(self, delivery_id, reference):
        raise NotImplementedError

    def fetch(self, reference):
        raise NotImplementedError

    def retrieve(self, pull_request):
        raise NotImplementedError

    def decide(self, pull_request, evidence):
        raise NotImplementedError

    def validate_request(self, reference):
        raise NotImplementedError

    def validate_evidence(self, evidence):
        raise NotImplementedError

    def validate_decision(self, decision, evidence):
        raise NotImplementedError

    def format(self, report):
        raise NotImplementedError

    def save(self, report):
        raise NotImplementedError

    def load(self, run_id):
        raise NotImplementedError

    def record(self, run_id, result):
        raise NotImplementedError


def test_factory_injects_implementations_behind_interfaces():
    settings = JsonConfigLoader().load(CONFIG)
    settings = settings.model_copy(
        update={
            "agent": settings.agent.model_copy(
                update={"browser_enabled": False, "behavior_checks_enabled": False}
            )
        }
    )
    dependencies = Dependencies()

    beans = AgentFactory.create(
        settings,
        webhook_receiver=dependencies,
        webhook_jobs=dependencies,
        pull_requests=dependencies,
        knowledge=dependencies,
        decision_model=dependencies,
        guardrails=dependencies,
        report_formatter=dependencies,
        run_store=dependencies,
        stage_recorder=dependencies,
    )

    assert beans.settings is settings
    assert beans.pipeline is not None
    assert beans.worker.pipeline is beans.pipeline
    assert beans.webhook_receiver is dependencies
    assert beans.webhook_jobs is dependencies
    assert beans.pull_requests is dependencies
    assert beans.knowledge is dependencies
    assert beans.decision_model is dependencies
    assert beans.stage_recorder is dependencies


def test_factory_requires_an_adapter_when_a_configured_stage_is_enabled():
    settings = JsonConfigLoader().load(CONFIG)
    enabled = settings.model_copy(
        update={"agent": settings.agent.model_copy(update={"browser_enabled": True})}
    )
    dependencies = Dependencies()

    with pytest.raises(ValueError, match="BrowserExplorer"):
        AgentFactory.create(
            enabled,
            webhook_receiver=dependencies,
            webhook_jobs=dependencies,
            pull_requests=dependencies,
            knowledge=dependencies,
            decision_model=dependencies,
            guardrails=dependencies,
            report_formatter=dependencies,
            run_store=dependencies,
            stage_recorder=dependencies,
        )
