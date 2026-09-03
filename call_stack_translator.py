import json
import sys
from pathlib import Path
from typing import Final, Iterable


class CodeMetaInformation:
    def __init__(
            self,
            blocks_info: dict,
            ti: dict
    ) -> None:
        self.blocks_info = blocks_info
        self.ti = ti
        self.source_lines: dict[str, list[str]] = {}

    def comment(self, block: int) -> str:
        return self.blocks_info[str(block)]['comment']

    def line_and_position(self, block: int) -> tuple[str, str]:
        """Returns position as <file>:<line> and line as string"""
        pos = self.blocks_info[str(block)]['pos']

        fid, ln, _, _ = self.ti['text'][pos]

        path = self.ti['paths'][str(fid)]

        if path not in self.source_lines:
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                self.source_lines[path] = f.readlines()

        line = self.source_lines[path][ln].strip().strip('{;').strip().strip('@')
        return line, f'{path}:{ln + 1}'


DEFAULT_BUILD_DIR: Final[str] = 'build'


def load_meta_info(build_dir: str = DEFAULT_BUILD_DIR) -> CodeMetaInformation | None:
    ti_file = f'{build_dir}/preprocessed.fa.ti'
    blocks_info_file = f'{build_dir}/cpp_blocks_info.json'

    try:
        with open(ti_file, 'r', encoding='utf-8') as file:
            ti = json.load(file)

        with open(blocks_info_file, 'r', encoding='utf-8') as file:
            blocks_info = json.load(file)

        return CodeMetaInformation(blocks_info, ti)
    except FileNotFoundError:
        print(
            f'Build files ({ti_file}, {blocks_info_file}) not found where expected. '
            f'Please ensure you build your program with --no-cleanup and '
            f'--build-dir={build_dir}'
        )
        sys.exit(1)


def call_stack_readable(
        history: Iterable[int],
        meta: CodeMetaInformation,
        short: bool = False
) -> list[str]:
    result: list[tuple[str, str]] = []

    prev: tuple[str, str] = ('', '')

    for block in history:
        line, position = meta.line_and_position(block)

        if (line, position) == prev:
            continue

        prev = (line, position)
        result.append((line, position))

    if short:
        return [line[4:line.find('(')] for line in (it[0] for it in result) if line.startswith('sub ')]

    return [f'{line} [{position}]' for line, position in reversed(result)]


ERROR_FILE_HEADER: Final[str] = '--- ADAPT ERROR BEGIN ---'
ERROR_FILE_FOOTER: Final[str] = '--- ADAPT ERROR END ---'


def main() -> None:
    meta = load_meta_info()

    error_file_paths = Path().cwd().glob('.adapt_error_*.dat')
    for path in error_file_paths:
        text: str = path.read_text(encoding='utf-8').strip()
        if not text.startswith(ERROR_FILE_HEADER):
            print(f'{path} ignored: wrong header')
            continue
        if not text.endswith(ERROR_FILE_FOOTER):
            print(f'{path} ignored: wrong footer')
            continue

        text = text.removeprefix(ERROR_FILE_HEADER).removesuffix(ERROR_FILE_FOOTER).strip()

        print(path)

        def key(line: str) -> str:
            return line.split('=')[0].strip()
        def value(line: str) -> str:
            return line.split('=')[1].strip()

        info = {
            key(line): value(line)
            for line in text.splitlines()
        }

        if 'message' in info:
            print(info['message'])

        if 'where' in info:
            file_line, function = info['where'].split()
            print(f'  in {function} [{file_line}]')

        if 'luna_call_stack' in info:
            luna_call_stack = [int(it.strip()) for it in info['luna_call_stack'].split()]
            for it in call_stack_readable(luna_call_stack, meta):
                print(f'  in {it}')
        else:
            print('No LuNA call stack')



if __name__ == '__main__':
    main()
