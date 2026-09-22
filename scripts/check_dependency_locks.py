"""Verify that every declared runtime dependency is pinned in the lock files.

The API declares its required packages in ``apps/api/pyproject.toml`` while a
fresh checkout installs from ``requirements.lock`` and
``requirements-runtime.lock``.  A dependency that exists only in pyproject is
silently missing at first startup, which is exactly how the PDF parser import
broke a lock-only environment.

This guard is intentionally pure Python: it never shells out to ``pip``, so it
also works in the release smoke environment and in CI without network access.

Exit status is non-zero when a declared dependency is absent from a lock file,
when the pinned version does not satisfy the declared specifier, or when a lock
file is not alphabetically ordered.
"""

from __future__ import annotations

import argparse
import itertools
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "apps" / "api" / "pyproject.toml"
LOCK_FILES = (
    ROOT / "apps" / "api" / "requirements-runtime.lock",
    ROOT / "apps" / "api" / "requirements.lock",
)

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*")
_OPERATOR = re.compile(r"(===|==|!=|~=|>=|<=|>|<)")
_RELEASE = re.compile(r"^(\d+(?:\.\d+)*)")
_PRE_LABELS = {"dev": 0, "a": 1, "alpha": 1, "b": 2, "beta": 2, "c": 3, "rc": 3, "pre": 3, "preview": 3}


class LockCheckError(RuntimeError):
    """Raised when a lock file cannot be parsed into pinned distributions."""


@dataclass(frozen=True)
class Requirement:
    """One ``[project] dependencies`` entry, reduced to what a lock can prove."""

    raw: str
    name: str
    specifiers: tuple[tuple[str, str], ...]

    @property
    def normalized_name(self) -> str:
        return normalize_name(self.name)


@dataclass(frozen=True)
class Version:
    """A PEP 440-comparable version, sufficient for the declared specifiers."""

    raw: str
    release: tuple[int, ...]
    pre: tuple[int, str] | None

    @staticmethod
    def parse(raw: str) -> Version:
        text = raw.strip()
        match = _RELEASE.match(text)
        if match is None:
            raise LockCheckError(f"unsupported version literal: {raw!r}")
        release = tuple(int(part) for part in match.group(1).split("."))
        remainder = text[match.end() :].lstrip("._-+")
        pre: tuple[int, str] | None = None
        pre_match = re.match(r"^([A-Za-z]+)\.?(\d*)", remainder)
        if pre_match is not None:
            label = pre_match.group(1).casefold()
            if label in _PRE_LABELS:
                pre = (_PRE_LABELS[label], pre_match.group(2))
        return Version(raw=text, release=release, pre=pre)

    def _key(self) -> tuple[tuple[int, ...], int, tuple[int, str]]:
        # Right-pad the release so ``1.19`` == ``1.19.0`` and ``1.19.0`` <
        # ``1.19.1``; a trailing zero segment is not a real difference.
        release = list(self.release)
        while len(release) < 4:
            release.append(0)
        return (tuple(release), 0 if self.pre is None else -1, self.pre or (0, ""))

    def __lt__(self, other: Version) -> bool:
        return self._key() < other._key()

    def __le__(self, other: Version) -> bool:
        return self._key() <= other._key()

    def __gt__(self, other: Version) -> bool:
        return self._key() > other._key()

    def __ge__(self, other: Version) -> bool:
        return self._key() >= other._key()

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        return self._key() == other._key()

    def __hash__(self) -> int:
        return hash((self.raw, self.release, self.pre))


def normalize_name(name: str) -> str:
    """Return the PEP 503 normalized form of a distribution name."""

    return re.sub(r"[-_.]+", "-", name).casefold()


def sort_key(name: str) -> str:
    """Return the ordering key the committed ``pip freeze``-style locks use.

    The lock files are hand-maintained ``pip freeze`` output, ordered by the
    case-insensitive distribution name exactly as printed.  That is
    ``pydantic`` < ``pydantic_core`` < ``pypdf`` < ``python-dotenv``, which PEP
    503 normalization would reorder because it rewrites ``_`` to ``-``.
    """

    return name.casefold()


def parse_requirement(raw: str) -> Requirement:
    """Parse one dependency string, tolerating extras, markers and spaces."""

    text = raw.strip()
    marker_index = text.find(";")
    if marker_index != -1:
        text = text[:marker_index].strip()
    match = _NAME.match(text)
    if match is None:
        raise LockCheckError(f"unsupported dependency declaration: {raw!r}")
    name = match.group(0)
    remainder = text[match.end() :]
    if remainder.startswith("["):
        closing = remainder.find("]")
        if closing == -1:
            raise LockCheckError(f"unterminated extras in dependency: {raw!r}")
        remainder = remainder[closing + 1 :]
    return Requirement(raw=raw.strip(), name=name, specifiers=tuple(_parse_specifiers(remainder, raw)))


def _parse_specifiers(remainder: str, raw: str) -> list[tuple[str, str]]:
    specifiers: list[tuple[str, str]] = []
    cursor = 0
    text = remainder
    while cursor < len(text):
        if text[cursor] in " \t,":
            cursor += 1
            continue
        match = _OPERATOR.match(text, cursor)
        if match is None:
            raise LockCheckError(f"unsupported version specifier {text[cursor:]!r} in dependency: {raw!r}")
        operator = match.group(1)
        cursor = match.end()
        if operator == "===":
            end = cursor
            while end < len(text) and text[end] not in ", \t":
                end += 1
            specifiers.append((operator, text[cursor:end].strip()))
            cursor = end
            continue
        value_match = _RELEASE.match(text[cursor:].lstrip())
        if value_match is None:
            raise LockCheckError(f"missing version after {operator!r} in dependency: {raw!r}")
        trimmed = text[cursor:]
        specifiers.append((operator, value_match.group(1)))
        cursor += len(trimmed) - len(trimmed.lstrip()) + value_match.end()
    return specifiers


def satisfies(version: Version, specifiers: tuple[tuple[str, str], ...]) -> bool:
    """Return whether ``version`` fulfils every declared specifier."""

    for operator, raw_expected in specifiers:
        expected = Version.parse(raw_expected)
        if operator == "==" and version != expected:
            return False
        if operator == "!=" and version == expected:
            return False
        if operator == ">=" and not version >= expected:
            return False
        if operator == "<=" and not version <= expected:
            return False
        if operator == ">" and not version > expected:
            return False
        if operator == "<" and not version < expected:
            return False
        if operator == "~=":
            # PEP 440 compatible release: ``~=1.19.0`` means ``==1.19.*`` and
            # ``~=1.19`` means ``==1.*``, so the segment BEFORE the last one is
            # the one that gets incremented.
            if not version >= expected:
                return False
            if len(expected.release) >= 2:
                ceiling = list(expected.release[:-2]) + [expected.release[-2] + 1]
            else:
                ceiling = [expected.release[0] + 1]
            ceiling_version = Version(raw=".".join(str(part) for part in ceiling), release=tuple(ceiling), pre=None)
            if not version < ceiling_version:
                return False
        if operator == "===" and version.raw != raw_expected:
            return False
    return True


def parse_lock(path: Path) -> dict[str, Version]:
    """Return ``{normalized name: pinned version}`` for one lock file."""

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise LockCheckError(f"cannot read lock file {path}: {error}") from error
    pinned: dict[str, Version] = {}
    names: list[str] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _NAME.match(stripped)
        if match is None:
            raise LockCheckError(f"{path}:{line_number}: not a pinned requirement: {stripped!r}")
        remainder = stripped[match.end() :]
        if ";" in remainder:
            remainder = remainder[: remainder.find(";")]
        if not remainder.startswith("==") or remainder.startswith("==="):
            raise LockCheckError(f"{path}:{line_number}: lock entries must pin with '==': {stripped!r}")
        name = match.group(0)
        try:
            version = Version.parse(remainder[2:].strip())
        except LockCheckError as error:
            raise LockCheckError(f"{path}:{line_number}: {error}") from error
        key = normalize_name(name)
        if key in pinned:
            raise LockCheckError(f"{path}:{line_number}: duplicate lock entry for {name}")
        pinned[key] = version
        names.append(sort_key(name))
    for previous, current in itertools.pairwise(names):
        # ``pip freeze`` prints both ``pytest`` and ``pytest-asyncio``; the
        # bare name legitimately precedes its own ``-``/``_``/``.`` siblings.
        if current < previous and not current.removeprefix(previous).startswith(("-", "_", ".")):
            raise LockCheckError(f"{path}: lock entries are not alphabetically sorted ({previous!r} before {current!r})")
    return pinned


def declared_requirements(pyproject: Path = PYPROJECT) -> list[Requirement]:
    """Return the ``[project] dependencies`` declarations from pyproject."""

    try:
        document = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise LockCheckError(f"cannot read {pyproject}: {error}") from error
    declared = document.get("project", {}).get("dependencies")
    if not isinstance(declared, list) or not declared:
        raise LockCheckError(f"{pyproject} declares no [project] dependencies")
    return [parse_requirement(str(item)) for item in declared]


def check_locks(pyproject: Path = PYPROJECT, locks: tuple[Path, ...] = LOCK_FILES) -> list[str]:
    """Return human-readable drift findings; an empty list means the locks agree."""

    findings: list[str] = []
    requirements = declared_requirements(pyproject)
    for lock_path in locks:
        try:
            pinned = parse_lock(lock_path)
        except LockCheckError as error:
            findings.append(str(error))
            continue
        for requirement in requirements:
            version = pinned.get(requirement.normalized_name)
            if version is None:
                findings.append(
                    f"{lock_path.name} is missing required dependency {requirement.name} (declared as {requirement.raw!r} in {pyproject.name})"
                )
                continue
            if not satisfies(version, requirement.specifiers):
                findings.append(
                    f"{lock_path.name} pins {requirement.name}=={version.raw}, which does not satisfy {requirement.raw!r} in {pyproject.name}"
                )
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pyproject", type=Path, default=PYPROJECT)
    parser.add_argument("--lock", type=Path, action="append", dest="locks")
    args = parser.parse_args(argv)
    locks = tuple(args.locks) if args.locks else LOCK_FILES
    findings = check_locks(args.pyproject, locks)
    if findings:
        print("dependency lock drift detected:", file=sys.stderr)
        for finding in findings:
            print(f"  - {finding}", file=sys.stderr)
        print(f"declared dependencies: {[item.raw for item in declared_requirements(args.pyproject)]}", file=sys.stderr)
        return 1
    for lock_path in locks:
        print(f"lock_ok={lock_path} entries={len(parse_lock(lock_path))}")
    print(f"dependency_locks=PASS declared={len(declared_requirements(args.pyproject))} locks={len(locks)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
