from pathlib import Path
from typing import Final, Iterable

import click

from trace.dtypes import Id, CF, CodeMetaInformation
from trace.formatting import call_history as call_stack_readable, full_df_name
from trace.load import load_meta_info, load_cf_info, load_id_map

DEFAULT_BUILD_DIR: Final[str] = 'build'

ERROR_FILE_HEADER: Final[str] = '--- ADAPT ERROR BEGIN ---'
ERROR_FILE_FOOTER: Final[str] = '--- ADAPT ERROR END ---'


def parse_luna_df_id(luna_df_id: str) -> Id:
    luna_df_id = luna_df_id.strip().lower()
    luna_df_id = luna_df_id.removeprefix('id<')
    luna_df_id = luna_df_id.removesuffix('>')
    return tuple(int(it.strip()) for it in luna_df_id.split(','))


def find_initializers(df_id: Id, cf_info: dict[Id, CF]) -> set[Id]:
    return {
        cf_id for cf_id, info in cf_info.items()
        if df_id in info.output
    }


def print_call_stack_readable(call_stack: Iterable[int], meta: CodeMetaInformation) -> None:
    for it in call_stack_readable(call_stack, meta):
        print(f'  in {it}')


@click.command()
@click.argument(
    'project-path',
    nargs=1,
    type=click.Path(
        dir_okay=True,
        file_okay=False,
        resolve_path=True,
        path_type=Path,
    )
)
def main(project_path: Path) -> None:
    meta = load_meta_info(build_dir=str(project_path / 'build'))

    cf_info = load_cf_info([str(it) for it in project_path.glob('cf.*.json.list')])
    df_info = load_id_map([str(it) for it in project_path.glob('id.*.map.list')])

    error_file_paths = project_path.glob('adapt_error_*.dat')
    i = 1
    for path in error_file_paths:
        text: str = path.read_text(encoding='utf-8').strip()
        if not text.startswith(ERROR_FILE_HEADER):
            print(f'{path} ignored: wrong header')
            continue
        if not text.endswith(ERROR_FILE_FOOTER):
            print(f'{path} ignored: wrong footer')
            continue

        text = text.removeprefix(ERROR_FILE_HEADER).removesuffix(ERROR_FILE_FOOTER).strip()

        print(f'[{i}] {path}')
        i += 1

        def key(line: str) -> str:
            return line.split('=')[0].strip()

        def value(line: str) -> str:
            return line.split('=')[1].strip()

        info = {
            key(line): value(line)
            for line in text.splitlines()
        }

        if 'error_code' in info:
            print(info['error_code'])
        else:
            print('No error code')

        if 'message' in info:
            print(info['message'])
        else:
            print('No message')

        if 'argument_name' in info:
            print(f'Argument {info["argument_name"]} of ', end='')

        if 'where' in info:
            file_line, function = info['where'].split()
            print(f'C++ function {function} [{file_line}]')

        if 'luna_call_stack' in info:
            luna_call_stack = [int(it.strip()) for it in info['luna_call_stack'].split()]
            print_call_stack_readable(luna_call_stack, meta)

        if 'luna_df_id' in info:
            df_id = parse_luna_df_id(info['luna_df_id'])
            df = df_info[df_id[:2]]
            full_name = full_df_name(df.name, df_id[2:])

            if 'argument_name' in info:
                print(f'Argument {info["argument_name"]} is DF {full_name}')

            print(f'Name {df.name} declared in {meta.comment(df.block)}')
            initializers = find_initializers(df_id, cf_info)
            if not initializers:
                print(f'No info about DF {full_name} initialization')
            else:
                print(f'DF {full_name} initialized in:')
                for cf_id in initializers:
                    print_call_stack_readable(cf_info[cf_id].history, meta)
                    print()


if __name__ == '__main__':
    main()
