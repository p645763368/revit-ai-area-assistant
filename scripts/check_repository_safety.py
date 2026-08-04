"""Fail CI when tracked files include forbidden artifacts or likely credentials."""

from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_SUFFIXES = {".rvt", ".rfa", ".rte", ".log"}
FORBIDDEN_PARTS = {"AI_Area_Assistant_Data", "screenshots", "logs"}
TEXT_SUFFIXES = {".json", ".md", ".py", ".toml", ".yaml", ".yml", ".txt"}

# Split sentinel strings so this scanner does not flag its own source.
SECRET_PATTERNS = (
    re.compile("-----BEGIN " + "(?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"(?i)authorization\s*[:=]\s*bearer\s+[A-Za-z0-9._-]{12,}"),
    re.compile(r"(?i)(?:api[_-]?key|secret[_-]?key)\s*[:=]\s*[\"'][^\"']{12,}[\"']"),
    re.compile("sk" + r"-[A-Za-z0-9_-]{20,}"),
)


def tracked_paths():
    output = subprocess.check_output(
        ["git", "ls-files", "-z"], cwd=ROOT
    ).decode("utf-8")
    return [Path(item) for item in output.split("\0") if item]


def violations(paths):
    findings = []
    for relative in paths:
        lowered_parts = {part.lower() for part in relative.parts}
        is_root_screenshot = len(relative.parts) == 1 and relative.name.lower().startswith("screenshot")
        if (
            relative.suffix.lower() in FORBIDDEN_SUFFIXES
            or {part.lower() for part in FORBIDDEN_PARTS}.intersection(lowered_parts)
            or is_root_screenshot
        ):
            findings.append(f"forbidden artifact: {relative.as_posix()}")
            continue
        absolute = ROOT / relative
        if relative.suffix.lower() not in TEXT_SUFFIXES or not absolute.is_file():
            continue
        content = absolute.read_text(encoding="utf-8", errors="replace")
        if any(pattern.search(content) for pattern in SECRET_PATTERNS):
            findings.append(f"possible credential: {relative.as_posix()}")
    return findings


def main():
    findings = violations(tracked_paths())
    if findings:
        print("Repository safety check failed:")
        print("\n".join(f"- {finding}" for finding in findings))
        return 1
    print("Repository safety check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
