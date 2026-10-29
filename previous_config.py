"""Workspace-scoped network settings captured before a configuration change."""
import copy
from workspace_state import read_section, write_section, _workspace_lock


def get_previous_config(name):
    with _workspace_lock:
        return copy.deepcopy(read_section("previous_network_configs").get(name))


def remember_config(name, previous):
    with _workspace_lock:
        configs = read_section("previous_network_configs")
        if previous:
            configs[name] = {key: copy.deepcopy(previous[key]) for key in
                             ("dhcp_raw", "ip", "subnet", "gateway", "dns") if key in previous}
        else:
            configs.pop(name, None)
        write_section("previous_network_configs", configs)
