import os
import shutil
from pathlib import Path


def _is_within_root(root: Path, target: Path) -> bool:
    root = root.resolve()
    target = target.resolve()
    try:
        common = Path(os.path.commonpath([str(root), str(target)]))
    except Exception:
        return False
    return common == root


def migrate_legacy_pico_artifacts(workspace_root, *, log=print):
    """Migrate legacy `.pico/` artifacts to `.pure/`.

    This is intentionally conservative:
    - Never writes outside `workspace_root`.
    - Moves artifacts when possible (fast, preserves data), but only for known
      artifact subdirectories.
    - Leaves unknown files under `.pico/` intact.
    """
    root = Path(workspace_root)
    pico_dir = root / ".pico"
    pure_dir = root / ".pure"

    if not pico_dir.exists():
        return {"migrated": False, "reason": "no_legacy_dir"}
    if not pico_dir.is_dir():
        return {"migrated": False, "reason": "legacy_not_dir"}

    # Safety: refuse to touch paths that resolve outside the workspace root.
    if not _is_within_root(root, pico_dir) or not _is_within_root(root, pure_dir):
        return {"migrated": False, "reason": "path_escape"}

    pure_dir.mkdir(parents=True, exist_ok=True)

    moved = []
    merged = []
    skipped = []
    for name in ("runs", "sessions", "memory"):
        src = pico_dir / name
        if not src.exists():
            continue
        dst = pure_dir / name
        if not dst.exists():
            shutil.move(str(src), str(dst))
            moved.append(name)
            continue
        # Merge: move files that don't exist yet in destination.
        for path in src.rglob("*"):
            rel = path.relative_to(src)
            target = dst / rel
            if path.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if target.exists():
                skipped.append(str(rel).replace("\\", "/"))
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(target))
            merged.append(str(rel).replace("\\", "/"))

    # If legacy dir is now empty, remove it.
    try:
        if pico_dir.exists() and not any(pico_dir.iterdir()):
            pico_dir.rmdir()
    except Exception:
        pass

    if moved or merged:
        log("[Pure] Migrated legacy pico artifacts to .pure/")
    return {
        "migrated": bool(moved or merged),
        "moved_roots": moved,
        "merged_files": merged,
        "skipped_files": skipped,
    }

