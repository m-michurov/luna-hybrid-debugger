from typing import Dict, Iterable, List, Set, Tuple, Union

from trace.dtypes import AwaitedDF, CallHistory, CF, Id

STYLE_YELLOW_BOLD: str = "\033[1;33m"
STYLE_RESET: str = "\033[0m"
INDENT: str = ' ' * 4


def c(*args: object) -> Union[Iterable[str], str]:
    """
    Makes strings colored.

    Args:
        *args: strings to colorize.

    Returns:
        Strings provided in args with "\033[1;33m" prepended and "\033[0m" appended.
    """
    if len(args) > 1:
        return (STYLE_YELLOW_BOLD + str(it) + STYLE_RESET for it in args)
    return STYLE_YELLOW_BOLD + str(args[0]) + STYLE_RESET


def separator() -> None:
    print(' - ' * 4 + '\n')


def longest_common_prefix(ids: List[Id]) -> Id:
    some_id = ids[0]
    min_len: int = min(map(len, ids))
    max_common_prefix_len: int = 0

    for i in range(
            min_len - 1,  # start
            -1,  # stop
            -1  # step
    ):
        if len(set(it[:i] for it in ids)) == 1:
            max_common_prefix_len = i
            break

    return some_id[:max_common_prefix_len]


def group_ids(ids: List[Id]) -> Tuple[List[Tuple[Id, Id]], int]:
    """
    Groups Ids into ranges.

    Args:
        ids: list of potentially sequential Ids.

    Returns:
        List of ranges in form of (begin, end) and a step.
    """
    if not ids:
        return [], 0

    if len(ids) == 1:
        return [(ids[0], ids[0])], 0

    ids = sorted(ids)
    ids_count = len(ids)

    # For now it works only with ids with same length
    # Possible solution:
    # Group ids by lengths and call self recursively for each group
    id_lengths = set(map(len, ids))
    if len(id_lengths) > 1:
        return [(it, it) for it in ids], 0

    adjacent_steps = [
        ids[i + 1][-1] - ids[i][-1]
        for i in range(ids_count - 1)
        if ids[i + 1][:-1] == ids[i][:-1]
    ]
    if not adjacent_steps:
        return [(it, it) for it in ids], 0

    step: int = min(adjacent_steps)

    result: List[Tuple[Id, Id]] = []
    start: Id = ids[0]
    previous: Id = start
    current: Id = start

    for current in ids[1:]:
        if previous[:-1] == current[:-1] and previous[-1] + step == current[-1]:
            previous = current
        else:

            result.append((start, previous))
            start, previous = current, current

    result.append((start, current))

    return result, step


def group_dfs(awaited: Iterable[AwaitedDF]) -> Dict[Id, Tuple[AwaitedDF, Set[Id]]]:
    result: Dict[Id, Tuple[AwaitedDF, Set[Id]]] = {
        df.idx[:2]: (df, set()) for df in awaited
    }

    for df in awaited:
        base_id: Id = df.idx[:2]

        _, ids = result[base_id]
        ids.add(df.idx)

    return result


def group_cfs(
        pending: Dict[Id, List[AwaitedDF]],
        cfs: Dict[Id, CF]
) -> Dict[CallHistory, Tuple[int, List[AwaitedDF]]]:
    """
    Groups pending CFs by call history.

    Args:
        pending: pending CFs.
        cfs: CF info.

    Returns:
        For each call history a number of CFs and a list of all awaited DFs is returned.
    """

    result: Dict[CallHistory, Tuple[int, List[AwaitedDF]]] = {
        tuple(cfs[cf_id].history): (0, []) for cf_id in pending
    }

    for cf_id, awaited in pending.items():
        cf = cfs[cf_id]
        history = tuple(cf.history)

        repeated_times, current_awaited = result[history]

        current_awaited.extend(awaited)

        result[history] = (repeated_times + 1, current_awaited)

    return result
