# TODO Person 1
# resolve(dependencies: list) -> lockfile dict
# parse_constraint(constraint_str) -> comparable object
# satisfies(version, constraint) -> bool
# walk_transitive(name, constraint, registry_metadata) -> dict

def resolve(dependencies: list) -> dict:
    raise NotImplementedError

def satisfies(version: str, constraint: str) -> bool:
    raise NotImplementedError