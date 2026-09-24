from typing import Dict, Set, List, Tuple, Iterable

from trace.dtypes import Id, CodeMetaInformation, DFInfo, AwaitedDF, CF, CallHistory
from trace.util import c, INDENT, separator, group_dfs, group_ids
from trace.hang_analysis import find_potential_initializers


def full_df_name(
        name: str,
        indices: Iterable[int]
) -> str:
    return name + ''.join(f'[{idx}]' for idx in indices)


def print_double_initialization_report(
        double_initialized_dfs: Dict[Id, Set[Id]],
        cfs: Dict[Id, CF],
        dfs: Dict[Id, DFInfo],
        meta: CodeMetaInformation
) -> None:
    if not double_initialized_dfs:
        return

    print(f'Following DFs are {c("initialized multiple times")}:\n')

    for df_id, cf_ids in double_initialized_dfs.items():
        initializing_cfs = map(lambda cf_id: cfs[cf_id], cf_ids)

        df_info: DFInfo = dfs[df_id[:2]]
        name = full_df_name(df_info.name, df_id[2:])
        declaration = meta.comment(df_info.block)

        print(f'{c(name)} (declared in {c(declaration)}) in')
        for cf in initializing_cfs:
            long_call_history: List[str] = call_history(cf.history, meta)
            print(INDENT + f'\n{INDENT * 2}in '.join(long_call_history), end='\n' * 2)

        print()
        separator()


def call_history(
        history: Iterable[int],
        meta: CodeMetaInformation,
        short: bool = False
) -> List[str]:
    result: List[Tuple[str, str]] = []

    prev: Tuple[str, str] = ('', '')

    for block in history:
        line, position = meta.line_and_position(block)

        if (line, position) == prev:
            continue

        prev = (line, position)
        result.append((line, position))

    if short:
        return [line[4:line.find('(')] for line in (it[0] for it in result) if line.startswith('sub ')]

    return [f'{line} [{position}]' for line, position in reversed(result)]


def single_df_description(
        awaited_df: AwaitedDF,
        df_info: DFInfo,
        meta: CodeMetaInformation
) -> str:
    indices = awaited_df.idx[2:]

    local_full_name = full_df_name(
        awaited_df.local_name, indices[-awaited_df.indices_count:])
    real_full_name = full_df_name(df_info.name, indices)

    result = f'{c(local_full_name)}'
    if awaited_df.expression != local_full_name:
        result += f' ({awaited_df.expression})'

    result += '\n'
    result += f'{INDENT * 2}full name {c(real_full_name)}, ' \
              f'{c(df_info.name)} declared in {c(meta.comment(df_info.block))}'

    return result


def df_range_description(
        awaited_df: AwaitedDF,
        df_info: DFInfo,
        awaited_ids: List[Id],
        meta: CodeMetaInformation
) -> str:
    aliased_name = full_df_name(
        df_info.name, awaited_df.idx[2:-awaited_df.indices_count])
    declaring_block = meta.comment(df_info.block)

    result = f'{c(awaited_df.expression)} (where {c(awaited_df.local_name)} is {c(aliased_name)},' \
             f' {c(df_info.name)} declared in {c(declaring_block)}):\n'

    id_ranges, step = group_ids(awaited_ids)
    if step > 1:
        result += f'{INDENT}Index step is {step}:\n'

    for start, end in id_ranges:
        start_name = full_df_name(
            awaited_df.local_name, start[-awaited_df.indices_count:])
        end_name = full_df_name(awaited_df.local_name,
                                end[-awaited_df.indices_count:])

        full_start_name = full_df_name(df_info.name, start[2:])
        full_end_name = full_df_name(df_info.name, end[2:])

        result += f'{INDENT * 2}{c(start_name)}..{c(end_name)} ({c(full_start_name)}..{c(full_end_name)})\n'

    return result


def print_awaited_dfs(
        grouped_dfs: Dict[Id, Tuple[AwaitedDF, Set[Id]]],
        dfs: Dict[Id, DFInfo],
        cfs: Dict[Id, CF],
        meta: CodeMetaInformation
) -> None:
    for base_id, (awaited_df, awaited_ids) in grouped_dfs.items():
        df: DFInfo = dfs[base_id]
        awaited_ids = sorted(awaited_ids)

        single: bool = len(awaited_ids) == 1

        if single:
            print(f'{INDENT}Awaited DF {single_df_description(awaited_df, df, meta)}')
        else:
            print(
                f'{INDENT}Awaited DFs {df_range_description(awaited_df, df, awaited_ids, meta)}')

        potential_initializers: Set[CallHistory] = find_potential_initializers(
            base_id, cf_info=cfs)
        if not potential_initializers:
            continue

        print(f'{INDENT}Maybe it should have been initialized here:')
        long_histories = [call_history(history, meta)
                          for history in potential_initializers]
        formatted_histories = [
            f'{INDENT * 2}' + f'\n{INDENT * 2}in '.join(long_history) for long_history in long_histories
        ]
        print(f'\n\n{INDENT}Or here:\n'.join(formatted_histories) + '\n')


def print_hang_report(
        grouped_cfs: Dict[Tuple[int], Tuple[int, List[AwaitedDF]]],
        dfs: Dict[Id, DFInfo],
        cfs: Dict[Id, CF],
        meta: CodeMetaInformation
) -> None:
    for history, (repeated_times, awaited) in grouped_cfs.items():
        block: int = history[-1]
        is_repeated: bool = repeated_times > 1

        long_call_history: List[str] = call_history(history[:-1], meta)
        short_call_history: List[str] = call_history(history, meta, short=True)

        line, position = meta.line_and_position(block)

        grouped_dfs: Dict[Id, Tuple[AwaitedDF, Set[Id]]] = group_dfs(awaited)

        print(f'{line} [{position}] never finished',
              end='' if is_repeated else '\n')
        if is_repeated:
            print(f' ({c(repeated_times)} instances)')

        print(' -> '.join(short_call_history))
        print(f'{INDENT}in ' +
              f'\n{INDENT}in '.join(long_call_history), end='\n' * 2)

        print_awaited_dfs(grouped_dfs, dfs, cfs, meta)

        separator()
