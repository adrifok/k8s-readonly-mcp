from datetime import UTC, datetime
from types import SimpleNamespace as N

import pytest

import k8s_readonly_mcp as m


def container(ready, restarts):
    return N(ready=ready, restart_count=restarts)


def event(hour, has_last_timestamp):
    ts = datetime(2026, 1, 1, hour, tzinfo=UTC)
    return N(
        type="Warning", reason=f"r{hour}", message="m", count=1,
        involved_object=N(kind="Pod", name="p"),
        last_timestamp=ts if has_last_timestamp else None,
        event_time=None, metadata=N(creation_timestamp=ts),
    )


def deployment(replicas, updated, available, desired=3):
    status = N(replicas=replicas, observed_generation=2, updated_replicas=updated,
               available_replicas=available, conditions=None)
    return N(metadata=N(generation=2), spec=N(replicas=desired), status=status)


def test_list_pods_readiness_and_restarts(monkeypatch):
    pods = [
        N(metadata=N(name="ok"), spec=N(node_name="n1"),
          status=N(phase="Running", container_statuses=[container(True, 1), container(True, 2)])),
        N(metadata=N(name="pending"), spec=N(node_name=None),
          status=N(phase="Pending", container_statuses=None)),
    ]
    monkeypatch.setattr(m.client, "CoreV1Api", lambda: N(list_namespaced_pod=lambda ns: N(items=pods)))
    ok, pending = m.list_pods()
    assert ok["ready"] and ok["restarts"] == 3
    assert not pending["ready"] and pending["restarts"] == 0


def test_list_events_newest_first_and_limit_clamped(monkeypatch):
    events = [event(1, True), event(3, False), event(2, True)]
    api = N(list_namespaced_event=lambda ns, field_selector=None: N(items=list(events)))
    monkeypatch.setattr(m.client, "CoreV1Api", lambda: api)
    assert [e["reason"] for e in m.list_events(limit=500)] == ["r3", "r2", "r1"]
    assert len(m.list_events(limit=-5)) == 1


# (replicas, updated, available, complete); replicas > updated means old pods still alive
@pytest.mark.parametrize("replicas,updated,available,complete", [(3, 3, 2, False), (4, 3, 3, False), (3, 3, 3, True)])
def test_rollout_complete_only_when_old_pods_are_gone(monkeypatch, replicas, updated, available, complete):
    d = deployment(replicas, updated, available)
    monkeypatch.setattr(m.client, "AppsV1Api", lambda: N(read_namespaced_deployment=lambda n, ns: d))
    assert m.rollout_status("x")["complete"] is complete
