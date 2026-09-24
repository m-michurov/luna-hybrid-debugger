import argparse
import re
import sys
from typing import Dict, List, Set, Tuple

from double_init import find_double_initializations
from dtypes import Id, DFInfo, CF, AwaitedDF, CodeMetaInformation, CallHistory
from formatting import print_double_initialization_report, print_hang_report
from load import load_cf_info, load_id_map, load_pending, load_meta_info
from hang_analysis import build_dependency_graph, find_independent
from util import group_cfs, c


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog='luna_trace', description='Analyze trace files and report errors.')
    parser.add_argument('logs', type=str, nargs='+',
                        help='trace input files (Luna5 list files or Luna6 EVLOG/JSONL)')
    parser.add_argument(
        '--all', action='store_true',
        help='print all hang CFs; default: print only those which do not have data dependencies on other hang CFs')
    parser.add_argument(
        '--no-double-init', action='store_true',
        help='do not look for double initialization errors (will probably increase performance)'
    )
    parser.add_argument(
        '--build-dir', type=str, default='build',
        help='directory with generated build metadata '
             '(preprocessed.fa.ti and cpp_blocks_info.json)'
    )

    return parser.parse_args(argv)


def _select_input_files(logs: List[str]) -> Tuple[List[str], List[str], List[str], str]:
    # Luna5 canonical file names
    pending_files: List[str] = [
        s for s in logs
        if re.search(r'(^|[\\/])pending\.\d+\.json\.list$', s)
    ]
    id_info_files: List[str] = [
        s for s in logs
        if re.search(r'(^|[\\/])id\.\d+\.map\.list$', s)
    ]
    cf_info_files: List[str] = [
        s for s in logs
        if re.search(r'(^|[\\/])cf\.\d+\.json\.list$', s)
    ]

    if pending_files or id_info_files or cf_info_files:
        # Mixed mode support:
        # if some categories use Luna5 file names, keep them;
        # for missing categories fallback to all provided files.
        return (
            pending_files or logs,
            id_info_files or logs,
            cf_info_files or logs,
            'mixed'
        )

    # Fallback for Luna6/EVLOG-like format: try parsing all files.
    return logs, logs, logs, 'auto'


def main(argv: List[str]) -> None:
    args = parse_args(argv)

    pending_file_names, id_info_file_names, cf_info_file_names, _ = _select_input_files(args.logs)

    cf_info: Dict[Id, CF] = load_cf_info(cf_info_file_names)
    df_info: Dict[Id, DFInfo] = load_id_map(id_info_file_names)
    pending: Dict[Id, List[AwaitedDF]] = load_pending(pending_file_names)

    if not cf_info:
        print(
            'Cannot find CF trace info. Expected either: '
            'Luna5 files cf.*.json.list or EVLOG/JSON records with CF history/dependencies.'
        )
        sys.exit(1)
    if not df_info:
        print(
            'Cannot find DF id map. Expected either: '
            'Luna5 files id.*.map.list or EVLOG/JSON records with {id, name, block}.'
        )
        sys.exit(1)

    meta: CodeMetaInformation = load_meta_info(args.build_dir)

    # Search for `Double Initialization Errors`
    if not args.no_double_init:
        double_initializations = find_double_initializations(cfs=cf_info)
        print_double_initialization_report(
            double_initializations,
            cfs=cf_info,
            dfs=df_info,
            meta=meta
        )

    initial_pending_count: int = len(pending)

    if not args.all:
        # Search `Hang Causes`
        dependencies: Dict[Id, Set[Id]] = build_dependency_graph(
            pending,
            cfs=cf_info
        )
        hang_causes: Set[Id] = find_independent(dependencies)

        if hang_causes:
            print(f'Following CFs appear to be the {c("root cause")} of the system hang:\n')
            pending = {cf_id: pending[cf_id] for cf_id in hang_causes}

        if not hang_causes and pending:
            print(f'Cannot find a single root cause: hang CFs probably have {c("cyclic dependencies")}!\n')

    grouped_cfs: Dict[CallHistory, Tuple[int, List[AwaitedDF]]] = group_cfs(pending, cfs=cf_info)

    print_hang_report(
        grouped_cfs,
        dfs=df_info,
        cfs=cf_info,
        meta=meta
    )

    extra_count: int = initial_pending_count - len(pending)
    if extra_count:
        print(f'{c(extra_count)} more CFs never finished but had data dependencies on other hang CFs\n')


if __name__ == '__main__':
    main(sys.argv[1:])
