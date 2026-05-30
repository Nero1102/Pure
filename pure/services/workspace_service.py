from ..core.workspace import WorkspaceContext


class WorkspaceService:
    def __init__(self, agent):
        self.agent = agent

    def build_context(self, cwd, repo_root_override=None):
        return WorkspaceContext.build(cwd, repo_root_override=repo_root_override)

    def refresh_prefix(self, force=False):
        previous_hash = getattr(getattr(self.agent, "prefix_state", None), "hash", None)
        previous_workspace_fingerprint = getattr(getattr(self.agent, "prefix_state", None), "workspace_fingerprint", None)

        refreshed_workspace = self.build_context(self.agent.root)
        refreshed_workspace_fingerprint = refreshed_workspace.fingerprint()
        workspace_changed = force or refreshed_workspace_fingerprint != previous_workspace_fingerprint
        if workspace_changed:
            self.agent.workspace = refreshed_workspace

        prefix_state = self.agent.build_prefix() if workspace_changed or force or previous_hash is None else self.agent.prefix_state
        prefix_changed = force or previous_hash != prefix_state.hash
        if prefix_changed:
            self.agent._apply_prefix_state(prefix_state)

        self.agent._last_prefix_refresh = {
            "workspace_changed": workspace_changed,
            "prefix_changed": prefix_changed,
        }
        return dict(self.agent._last_prefix_refresh)

