from typing import Dict, Set

from dtypes import Id, CF


def find_double_initializations(cfs: Dict[Id, CF]) -> Dict[Id, Set[Id]]:
    """
    Returns:
        Mapping between DF Id and a set of initializer blocks
    """
    result: Dict[Id, Set[Id]] = {}

    items = list(filter(lambda it: it[1].output, cfs.items()))

    for i, (cf1_id, cf1) in enumerate(items):
        for (cf2_id, cf2) in items[i + 1:]:
            cf1_id: Id
            cf2_id: Id
            cf1: CF
            cf2: CF

            out1 = cf1.output
            out2 = cf2.output

            intersection = out1.intersection(out2)

            if not intersection:
                continue

            for df in intersection:
                df = tuple(df)
                result[df] = result.get(df) or set()
                result[df].add(cf1_id)
                result[df].add(cf2_id)

    return result
