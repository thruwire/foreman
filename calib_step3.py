"""Calibration fix 1b: populate untracked_evidence in ObservationBuilder.build."""

src = open("src/foreman/observation.py").read()

helper = '''

def _untracked_evidence(repository: Path, git_status: str, limit: int) -> str:
    """Bounded head-of-content for untracked files named in git_status.

    hermes-style workers write test files as new (untracked) files; `git diff`
    never shows them, leaving the supervisor without test evidence. Include a
    bounded excerpt of each untracked text file (up to 3 files, ~4k chars
    total) so assessments can see new tests/source. Binary-looking files are
    skipped. Read-only: the repository is never mutated.
    """
    paths: list[str] = []
    for line in git_status.splitlines():
        line = line.strip()
        if line.startswith("?? "):
            candidate = line[3:].strip().strip('"')
            if candidate and not candidate.endswith("/"):
                paths.append(candidate)
    if not paths:
        return ""

    per_file = max(500, limit // 3)
    chunks: list[str] = []
    collected = 0
    for name in paths[:3]:
        path = repository / name
        try:
            if not path.is_file() or path.stat().st_size > 1_000_000:
                continue
            raw = path.read_bytes()
        except OSError:
            continue
        if b"\\x00" in raw[:4096]:
            continue  # binary
        text = raw.decode("utf-8", errors="replace")
        excerpt = text[:per_file]
        if len(text) > per_file:
            excerpt += f"\\n... (truncated, {len(text)} chars total)"
        chunks.append(f"--- untracked: {name} ---\\n{excerpt}")
        collected += len(excerpt)
        if collected >= limit:
            break
    return "\\n".join(chunks)

'''

if "_untracked_evidence" not in src:
    # insert helper before ObservationBuilder
    src = src.replace(
        "class ObservationBuilder:",
        helper + "\n\nclass ObservationBuilder:",
    )
    # populate the field in build()
    src = src.replace(
        '            git_status=git_status,\n            git_diff=git_diff,',
        '            git_status=git_status,\n'
        '            git_diff=git_diff,\n'
        '            untracked_evidence=_untracked_evidence(repository, git_status, min(self.config.diff_limit, 12_000)),',
    )
    open("src/foreman/observation.py", "w").write(src)
    print("builder patched")
else:
    print("builder already patched")