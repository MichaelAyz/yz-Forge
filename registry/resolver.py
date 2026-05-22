# resolver.py — Semver parser + dependency resolver for the Forge registry.

import re
import json
from typing import Optional
import registry.metadata as metadata

# --- Custom Exception classes ---
class ConflictError(Exception):
    pass

class CycleError(Exception):
    pass


# --- SemVer Version Class ---

SEMVER_REGEX = re.compile(
    r'^v?(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)'
    r'(?:-(?P<prerelease>(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?'
    r'(?:\+(?P<buildmetadata>[0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$'
)

class Version:
    def __init__(self, major: int, minor: int, patch: int, prerelease: str = "", build: str = ""):
        self.major = major
        self.minor = minor
        self.patch = patch
        self.prerelease = prerelease
        self.build = build

    @classmethod
    def parse(cls, version_str: str) -> "Version":
        version_str = version_str.strip()
        if version_str.startswith('v'):
            version_str = version_str[1:]
        m = SEMVER_REGEX.match(version_str)
        if not m:
            raise ValueError(f"Invalid semver version: {version_str}")
        gd = m.groupdict()
        return cls(
            major=int(gd["major"]),
            minor=int(gd["minor"]),
            patch=int(gd["patch"]),
            prerelease=gd["prerelease"] or "",
            build=gd["buildmetadata"] or ""
        )

    def _compare_key(self):
        if self.prerelease:
            prerelease_parts = []
            for part in self.prerelease.split('.'):
                if part.isdigit():
                    prerelease_parts.append((0, int(part)))  # 0 ensures numbers sort before strings
                else:
                    prerelease_parts.append((1, part))       # 1 for string sorting
            has_prerelease = 0
        else:
            has_prerelease = 1
            prerelease_parts = []

        return (self.major, self.minor, self.patch, has_prerelease, prerelease_parts)

    def __lt__(self, other: "Version") -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        return self._compare_key() < other._compare_key()

    def __le__(self, other: "Version") -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        return self._compare_key() <= other._compare_key()

    def __gt__(self, other: "Version") -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        return self._compare_key() > other._compare_key()

    def __ge__(self, other: "Version") -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        return self._compare_key() >= other._compare_key()

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        return self._compare_key() == other._compare_key()

    def __repr__(self) -> str:
        base = f"v{self.major}.{self.minor}.{self.patch}"
        if self.prerelease:
            base += f"-{self.prerelease}"
        if self.build:
            base += f"+{self.build}"
        return base


# --- Constraint Parsing and Matching ---

def parse_constraint_term(term_str: str):
    term_str = term_str.strip()
    match = re.match(r'^(?P<op>\^|~|>=|<=|>|<|=)?\s*(?P<ver>.*)$', term_str)
    if not match:
        raise ValueError(f"Invalid constraint term: {term_str}")
    op = match.group("op") or "="
    ver_str = match.group("ver").strip()

    # Split to check for partial versions
    parts = ver_str.split('.')
    major = int(parts[0]) if len(parts) > 0 and parts[0].isdigit() else None
    minor = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
    
    # Extract patch and prerelease/build from the 3rd part
    patch = None
    prerelease = ""
    build = ""
    if len(parts) > 2:
        patch_part = parts[2]
        # split by - or +
        subparts = patch_part.split('-', 1)
        if len(subparts) > 1:
            patch_str = subparts[0]
            prerelease_build = subparts[1].split('+', 1)
            prerelease = prerelease_build[0]
            if len(prerelease_build) > 1:
                build = prerelease_build[1]
        else:
            build_parts = patch_part.split('+', 1)
            patch_str = build_parts[0]
            if len(build_parts) > 1:
                build = build_parts[1]
        
        if patch_str.isdigit():
            patch = int(patch_str)

    if major is None:
        raise ValueError(f"Invalid version in constraint term: {ver_str}")

    # Pad missing parts with 0 for Version instantiation
    minor_val = minor if minor is not None else 0
    patch_val = patch if patch is not None else 0
    full_ver = Version(major, minor_val, patch_val, prerelease, build)

    return op, full_ver, major, minor, patch


def satisfies_term(v: Version, op: str, full_ver: Version, major: int, minor: Optional[int], patch: Optional[int]) -> bool:
    # Rule: If v is a prerelease version, it is only allowed if full_ver also has a prerelease,
    # AND their major, minor, patch match exactly.
    if v.prerelease:
        if not full_ver.prerelease:
            return False
        if v.major != full_ver.major or v.minor != full_ver.minor or v.patch != full_ver.patch:
            return False

    if op == "=":
        if minor is None:
            return v.major == major
        elif patch is None:
            return v.major == major and v.minor == minor
        else:
            return v == full_ver
    elif op == "^":
        if v < full_ver:
            return False
        if major > 0:
            return v.major == major
        elif minor is not None and minor > 0:
            return v.major == 0 and v.minor == minor
        else:
            if minor is None:
                return v.major == 0
            elif patch is not None:
                return v.major == 0 and v.minor == 0 and v.patch == patch
            else:
                return v.major == 0 and v.minor == 0
    elif op == "~":
        if v < full_ver:
            return False
        if minor is None:
            return v.major == major
        else:
            return v.major == major and v.minor == minor
    elif op == ">=":
        return v >= full_ver
    elif op == "<=":
        return v <= full_ver
    elif op == ">":
        return v > full_ver
    elif op == "<":
        return v < full_ver
    return False


def satisfies(version: str, constraint: str) -> bool:
    try:
        v = Version.parse(version)
    except ValueError:
        return False

    constraint = constraint.strip()
    if constraint in ("", "*"):
        return not v.prerelease

    # Split the constraint string by spaces or commas
    term_strs = re.split(r'[\s,]+', constraint)
    for term_str in term_strs:
        if not term_str:
            continue
        try:
            op, full_ver, major, minor, patch = parse_constraint_term(term_str)
        except ValueError:
            return False
        if not satisfies_term(v, op, full_ver, major, minor, patch):
            return False
    return True


# --- Backtracking Resolver ---

def _solve(queue: list, resolved: dict, constraints: dict) -> dict:
    if not queue:
        return resolved

    name, constraint, parent_path = queue[0]

    # Cycle Detection
    if name in parent_path:
        path_str = " -> ".join(parent_path + (name,))
        raise CycleError(f"Dependency cycle detected: {path_str}")

    # Track constraints for this package
    new_constraints = {k: list(v) for k, v in constraints.items()}
    if name not in new_constraints:
        new_constraints[name] = []
    new_constraints[name].append((constraint, parent_path))

    # If already resolved, check if it satisfies the new constraint
    if name in resolved:
        current_version = resolved[name]
        if satisfies(current_version, constraint):
            try:
                return _solve(queue[1:], resolved, new_constraints)
            except ConflictError:
                raise
        else:
            # We have a conflict because the already resolved version does not satisfy the new constraint.
            conflict_details = []
            for c, p in new_constraints[name]:
                path_str = " -> ".join(p) if p else "root"
                conflict_details.append(f"'{c}' from {path_str}")
            raise ConflictError(
                f"Version conflict for package '{name}': "
                f"Resolved version {current_version} does not satisfy all constraints: {', '.join(conflict_details)}"
            )

    # If not resolved, get all versions from metadata database
    versions = metadata.list_versions(name)
    
    # Parse and sort versions descending (highest version first)
    parsed_versions = []
    for v_str in versions:
        try:
            parsed_versions.append((Version.parse(v_str), v_str))
        except ValueError:
            continue
    parsed_versions.sort(key=lambda x: x[0], reverse=True)

    # Try candidate versions
    active_constraints = new_constraints[name]
    last_conflict = None
    for v_obj, v_str in parsed_versions:
        # Must satisfy all constraints on this package
        all_satisfied = True
        for c_str, p in active_constraints:
            if not satisfies(v_str, c_str):
                all_satisfied = False
                break
        
        if not all_satisfied:
            continue

        # Fetch dependency metadata for this candidate
        meta = metadata.get_artifact(name, v_str)
        if not meta:
            continue

        try:
            deps_list = json.loads(meta["deps_json"])
        except Exception:
            deps_list = []

        # Add dependencies to the front of the queue
        new_deps = []
        for dep in deps_list:
            dep_name = dep.get("name")
            dep_constraint = dep.get("version")
            if dep_name and dep_constraint:
                new_deps.append((dep_name, dep_constraint, parent_path + (name,)))

        # Update resolved mapping
        new_resolved = resolved.copy()
        new_resolved[name] = v_str

        try:
            result = _solve(new_deps + queue[1:], new_resolved, new_constraints)
            if result is not None:
                return result
        except ConflictError as e:
            last_conflict = e
            continue

    # No version worked -> conflict!
    if last_conflict is not None:
        raise last_conflict

    conflict_details = []
    for c, p in active_constraints:
        path_str = " -> ".join(p) if p else "root"
        conflict_details.append(f"'{c}' from {path_str}")
    raise ConflictError(
        f"Version conflict for package '{name}': No version satisfies all constraints: {', '.join(conflict_details)}"
    )


def resolve(dependencies: list) -> dict:
    """Resolve dependency constraints transitively.

    Args:
        dependencies: list of dict, e.g. [{"name": "lib-core", "version": "^1.0.0"}]

    Returns:
        A deterministic lockfile dict.
    """
    queue = [(d["name"], d["version"], ()) for d in dependencies]
    try:
        resolved = _solve(queue, {}, {})
    except CycleError as e:
        raise ValueError(str(e))
    except ConflictError as e:
        raise ValueError(str(e))

    # Build the lockfile
    packages = {}
    for name in sorted(resolved.keys()):
        version = resolved[name]
        meta = metadata.get_artifact(name, version)
        if not meta:
            raise ValueError(f"Metadata not found for resolved package {name}@{version}")

        try:
            deps_list = json.loads(meta["deps_json"])
        except Exception:
            deps_list = []

        packages[name] = {
            "version": version,
            "sha256": meta["sha256"],
            "dependencies": deps_list
        }

    return {"packages": packages}
