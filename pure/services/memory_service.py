from ..core import memory as memorylib


class MemoryService:
    def __init__(self, agent):
        self.agent = agent

    def invalidate_stale_memory(self):
        invalidated = self.agent.memory.invalidate_stale_file_summaries()
        self.agent.session["memory"] = self.agent.memory.to_dict()
        return invalidated

    def memory_text(self):
        return self.agent.memory.render_memory_text()

    def update_after_tool(self, name, args, result):
        if not self.agent.feature_enabled("memory"):
            return
        path = args.get("path")
        if not path:
            return

        canonical_path = self.agent.memory.canonical_path(path)
        if name in {"read_file", "write_file", "patch_file"}:
            self.agent.memory.remember_file(canonical_path)
        if name == "read_file":
            summary = memorylib.summarize_read_result(result)
            self.agent.memory.set_file_summary(canonical_path, summary)
            self.agent.memory.append_note(summary, tags=(canonical_path,), source=canonical_path)
        elif name in {"write_file", "patch_file"}:
            self.agent.memory.invalidate_file_summary(canonical_path)

    def note_tool(self, name, args, result):
        self.update_after_tool(name, args, result)

