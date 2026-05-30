from ..core import memory as memorylib
from ..core.workspace import clip, now


CHECKPOINT_SCHEMA_VERSION = "phase1-v1"


class CheckpointService:
    def __init__(self, agent):
        self.agent = agent

    def evaluate_resume_state(self):
        previous_resume_state = dict(self.agent.session.get("resume_state", {}) or {})
        invalidated = self.agent.memory_service.invalidate_stale_memory()
        checkpoint = self.agent.current_checkpoint()
        status = self.agent.CHECKPOINT_NONE_STATUS
        stale_paths = list(invalidated)
        mismatch_fields = []
        if checkpoint:
            if checkpoint.get("schema_version") not in {self.agent.CHECKPOINT_SCHEMA_VERSION, "phase1-v1"}:
                status = self.agent.CHECKPOINT_SCHEMA_MISMATCH_STATUS
            else:
                for item in checkpoint.get("key_files", []):
                    path = str(item.get("path", "")).strip()
                    if not path:
                        continue
                    expected = item.get("freshness")
                    current = memorylib.file_freshness(path, self.agent.root)
                    if expected != current and path not in stale_paths:
                        stale_paths.append(path)
                saved_identity = dict(checkpoint.get("runtime_identity", {}) or self.agent.session.get("runtime_identity", {}) or {})
                current_identity = self.agent.current_runtime_identity()
                identity_keys = (
                    "cwd",
                    "model",
                    "model_client",
                    "approval_policy",
                    "read_only",
                    "max_steps",
                    "max_new_tokens",
                    "feature_flags",
                    "shell_env_allowlist",
                    "workspace_fingerprint",
                    "tool_signature",
                )
                for key in identity_keys:
                    if key not in saved_identity:
                        continue
                    if saved_identity.get(key) != current_identity.get(key):
                        mismatch_fields.append(key)
                mismatch_fields.sort()
                if stale_paths:
                    status = self.agent.CHECKPOINT_PARTIAL_STALE_STATUS
                elif mismatch_fields:
                    status = self.agent.CHECKPOINT_WORKSPACE_MISMATCH_STATUS
                else:
                    status = self.agent.CHECKPOINT_FULL_VALID_STATUS

        resume_state = {
            "status": status,
            "stale_paths": stale_paths,
            "runtime_identity_mismatch_fields": mismatch_fields,
            "stale_summary_invalidations": max(
                len(invalidated),
                int(previous_resume_state.get("stale_summary_invalidations", 0))
                if status == self.agent.CHECKPOINT_PARTIAL_STALE_STATUS
                else 0,
            ),
        }
        self.agent.session["resume_state"] = resume_state
        self.agent.session["runtime_identity"] = self.agent.current_runtime_identity()
        return resume_state

    def create_checkpoint(self, task_state, user_message, trigger):
        state = self.agent.checkpoint_state()
        current = self.agent.current_checkpoint()
        checkpoint_id = "ckpt_" + self.agent.uuid4_hex()[:8]
        key_files = []
        freshness = {}
        for path in self.agent.memory.to_dict()["working"]["recent_files"]:
            file_freshness = memorylib.file_freshness(path, self.agent.root)
            freshness[path] = file_freshness
            key_files.append({"path": path, "freshness": file_freshness})
        checkpoint = {
            "checkpoint_id": checkpoint_id,
            "parent_checkpoint_id": current.get("checkpoint_id", "") if current else "",
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "created_at": now(),
            "task_id": task_state.task_id,
            "run_id": task_state.run_id,
            "step": int(task_state.tool_steps or 0),
            "current_goal": str(user_message),
            "completed": [task_state.final_answer] if task_state.final_answer else [],
            "excluded": [],
            "current_blocker": "" if str(task_state.stop_reason or "") in ("", "final_answer_returned") else str(task_state.stop_reason),
            "next_step": self.agent.infer_next_step(task_state),
            "key_files": key_files,
            "freshness": freshness,
            "summary": f"{trigger}: {clip(str(user_message), 120)}",
            "memory_snapshot": self.agent.memory.to_dict(),
            "workspace_hash": self.agent.workspace.fingerprint(),
            "last_trace_event": getattr(self.agent, "_last_trace_event", {}) or {},
            "runtime_metadata": {
                "trigger": trigger,
                "task_status": task_state.status,
                "stop_reason": task_state.stop_reason,
            },
            "runtime_identity": self.agent.current_runtime_identity(),
        }
        state["items"][checkpoint_id] = checkpoint
        state["current_id"] = checkpoint_id
        task_state.checkpoint_id = checkpoint_id
        self.agent.session["runtime_identity"] = checkpoint["runtime_identity"]
        self.agent.session_path = self.agent.session_store.save(self.agent.session)
        return checkpoint

    def list_checkpoints(self):
        state = self.agent.checkpoint_state()
        items = list((state.get("items", {}) or {}).values())
        return sorted(items, key=lambda item: str(item.get("created_at", "")))

    def load_checkpoint(self, checkpoint_id):
        checkpoint = (self.agent.checkpoint_state().get("items", {}) or {}).get(str(checkpoint_id))
        if not checkpoint:
            raise KeyError(f"checkpoint not found: {checkpoint_id}")
        return checkpoint

    def validate_checkpoint(self, checkpoint):
        errors = []
        if checkpoint.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
            errors.append("checkpoint schema mismatch")
        if checkpoint.get("workspace_hash") and checkpoint.get("workspace_hash") != self.agent.workspace.fingerprint():
            errors.append("workspace hash mismatch")
        runtime_identity = dict(checkpoint.get("runtime_identity", {}) or {})
        current_identity = self.agent.current_runtime_identity()
        if runtime_identity.get("model_client") and runtime_identity.get("model_client") != current_identity.get("model_client"):
            errors.append("runtime version mismatch")
        return {
            "valid": not errors,
            "errors": errors,
            "checkpoint_id": checkpoint.get("checkpoint_id", ""),
            "schema_version": checkpoint.get("schema_version", ""),
            "workspace_hash": checkpoint.get("workspace_hash", ""),
        }
