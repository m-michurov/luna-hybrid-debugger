from typing import Dict, List, Set

from trace.dtypes import AwaitedDF, Id, CF, CallHistory


def build_dependency_graph(
        pending: Dict[Id, List[AwaitedDF]],
        cfs: Dict[Id, CF]
) -> Dict[Id, Set[Id]]:
    dependencies: Dict[Id, Set[Id]] = {it: set() for it in pending}

    for this_cf_id, this_awaited in pending.items():
        this_awaited = set(it.idx for it in this_awaited)
        for other_cf_id in pending:
            other_cf = cfs[other_cf_id]
            other_out = set(other_cf.output)

            if this_awaited.intersection(other_out):
                dependencies[this_cf_id].add(other_cf_id)

    return dependencies


def find_independent(dependency_graph: Dict[Id, Set[Id]]) -> Set[Id]:
    return {cf_id for cf_id, dependencies in dependency_graph.items() if not dependencies}


def find_potential_initializers(
        df_id: Id,
        cf_info: Dict[Id, CF]
) -> Set[CallHistory]:
    df_id: Id = df_id[:2]

    result: Set[CallHistory] = set()

    for _, cf in cf_info.items():
        if any(it[:2] == df_id for it in cf.output):
            result.add(tuple(cf.history))

    return result
