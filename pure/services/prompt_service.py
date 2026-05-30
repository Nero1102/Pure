class PromptService:
    def __init__(self, agent):
        self.agent = agent

    def build_prompt_and_metadata(self, user_message):
        refresh = self.agent.workspace_service.refresh_prefix()
        self.agent.resume_state = self.agent.checkpoint_service.evaluate_resume_state()
        prompt, metadata = self.agent.context_manager.build(user_message)
        metadata.update(
            {
                "prefix_chars": len(self.agent.prefix),
                "workspace_chars": len(self.agent.workspace.text()),
                "memory_chars": len(self.agent.memory_service.memory_text()),
                "knowledge_context_chars": len(getattr(self.agent, "knowledge_context", "") or ""),
                "knowledge_source_count": len(getattr(self.agent, "knowledge_sources", []) or []),
                "history_chars": len(self.agent.history_text()),
                "request_chars": len(user_message),
                "tool_count": len(self.agent.tools),
                "workspace_docs": len(self.agent.workspace.project_docs),
                "recent_commits": len(self.agent.workspace.recent_commits),
                "prefix_hash": self.agent.prefix_state.hash,
                "prompt_cache_key": self.agent.prefix_state.hash,
                "workspace_fingerprint": self.agent.prefix_state.workspace_fingerprint,
                "tool_signature": self.agent.prefix_state.tool_signature,
                "workspace_changed": refresh["workspace_changed"],
                "prefix_changed": refresh["prefix_changed"],
                "prompt_cache_supported": bool(getattr(self.agent.model_client, "supports_prompt_cache", False)),
                "resume_status": self.agent.resume_state.get("status", self.agent.CHECKPOINT_NONE_STATUS),
                "stale_summary_invalidations": int(self.agent.resume_state.get("stale_summary_invalidations", 0)),
                "stale_paths": list(self.agent.resume_state.get("stale_paths", [])),
                "runtime_identity_mismatch_fields": list(self.agent.resume_state.get("runtime_identity_mismatch_fields", [])),
            }
        )
        metadata.update(self.agent.detected_secret_env_summary())
        return prompt, metadata
