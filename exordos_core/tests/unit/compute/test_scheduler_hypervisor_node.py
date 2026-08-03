import uuid as sys_uuid
from unittest import mock

from exordos_core.compute.scheduler import service


def test_a_hypervisor_node_is_not_a_workload_to_place():
    """The hypervisor's own node is skipped; the guests behind it are not.

    `register-agent` gives the hypervisor's agent a node for identity only; it
    is the machine, so scheduling it would fail and go ERROR on every pass.
    """
    host = sys_uuid.uuid4()
    guest = sys_uuid.uuid4()

    sched = service.SchedulerService.__new__(service.SchedulerService)
    sched._machine_filters = []
    sched._machine_weighters = []
    bundles = [mock.MagicMock(), mock.MagicMock()]
    for b, uuid in zip(bundles, (host, guest)):
        b.node.uuid = uuid
        b.node.node_type = "VM"

    with (
        mock.patch.object(
            service.SchedulerService, "_hypervisor_nodes", return_value={host}
        ),
        mock.patch.object(
            service.SchedulerService,
            "_get_unscheduled_nodes",
            return_value=tuple(bundles),
        ),
        mock.patch.object(
            service.SchedulerService, "_get_idle_machines", return_value=[]
        ),
    ):
        left = sched._schedule_on_existing_machines()

    # What survives the filter is what the scheduler will try to place.
    assert [b.node.uuid for b in left] == [guest], (
        "the hypervisor's own node was handed to the scheduler, which can "
        "only fail it and mark it ERROR"
    )
