import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Final

C_ASSERT_CPP_PATH: Final[Path] = Path(__file__).parent / 'c_assert.cpp'


class TextInfo:
    def __init__(self, text_info_json: dict) -> None:
        self._text_info: dict = text_info_json

    def file_name(self, position: int) -> str:
        fid, _, _, _ = self._text_info['text'][position]
        path: str = self._text_info['paths'][str(fid)]

        return re.sub(r'(\./)+', './', path)

    def line_number(self, position: int) -> int:
        _, ln, _, _ = self._text_info['text'][position]
        return ln + 1

    def file_and_line(self, position: int) -> tuple[str, int]:
        return self.file_name(position), self.line_number(position)


def ja_indexed_expression_to_str(expr: dict) -> str:
    assert 'id' == expr['type']

    ref = expr['ref']
    name, indices = ref[0], ref[1:]
    if not indices:
        return name

    return f'{name}[{', '.join(map(ja_expression_to_str, indices))}]'


JA_UNARY_OPERATORS: Final[set[str]] = {'neg', }
JA_BINARY_OPERATORS: Final[set[str]] = {
    '+', '-', '*', '/',
    '>', '>=', '<', '<=', '==', '!=',
    '&&', '||'
}


def ja_arith_expression_to_str(expr: dict) -> str:
    match expr['type']:
        case 'iconst':
            return str(expr['value'])
        case op if op in JA_BINARY_OPERATORS:
            lhs, rhs = map(ja_expression_to_str, expr['operands'])
            return f'({lhs} {op} {rhs})'
        case op if op in JA_UNARY_OPERATORS:
            operand = ja_expression_to_str(expr['operands'][0])
            return f'{op}{operand}'
        case unsupported_type:
            assert False, unsupported_type


def ja_expression_to_str(expr: dict) -> str:
    match expr['type']:
        case 'id':
            return ja_indexed_expression_to_str(expr)
        case 'iconst':
            return ja_arith_expression_to_str(expr)
        case op if op in JA_BINARY_OPERATORS or op in JA_UNARY_OPERATORS:
            return ja_arith_expression_to_str(expr)
        case unsupported_type:
            assert False, unsupported_type


def print_statement(statement: dict, indent: int = 0) -> None:
    print(f'{' ' * indent}{statement['type']}')

    indent += 1
    padding = ' ' * indent

    match statement['type']:
        case 'dfs':
            print(f'{padding}names=[{', '.join(statement['names'])}]')
        case 'exec':
            print(f'{padding}code={statement['code']}')
            print(f'{padding}args=[{', '.join(map(ja_expression_to_str, statement['args']))}]')
        case 'for':
            print(f'{padding}var={statement['var']}')
            print(f'{padding}first={ja_expression_to_str(statement['first'])}')
            print(f'{padding}last={ja_expression_to_str(statement['last'])}')

            for body_statement in statement['body']:
                print_statement(body_statement, indent + 1)
        case _:
            assert False, 'unknown statement'


def find_init_loops(sub: dict, program: dict) -> dict[str, list[dict]]:
    result: dict[str, list[dict]] = {}

    for statement in sub['body']:
        if statement['type'] != 'for':
            continue

        for_var_name = statement['var']

        for body_statement in statement['body']:
            # TODO handle initializations in while loops
            if body_statement['type'] != 'exec':
                continue

            code_fragment_name = body_statement['code']
            code_fragment = program[code_fragment_name]

            for formal, actual in zip(code_fragment['args'], body_statement['args']):
                if formal['type'] != 'name':
                    continue

                if len(actual['ref']) != 2:
                    continue

                base_name, index = actual['ref']
                if index['ref'] != [for_var_name]:
                    continue

                result[base_name] = result.get(base_name, []) + [statement]

    return result


def find_use_loops(sub: dict, program: dict) -> dict[str, list[dict]]:
    result: dict[str, list[dict]] = {}

    for statement in sub['body']:
        if statement['type'] != 'for':
            continue

        for_var_name = statement['var']

        for body_statement in statement['body']:
            # TODO handle consumptions in loop bounds, conditions, etc
            # TODO handle consumptions in subexpressions
            if body_statement['type'] != 'exec':
                continue

            code_fragment_name = body_statement['code']
            code_fragment = program[code_fragment_name]

            for formal, actual in zip(code_fragment['args'], body_statement['args']):
                if formal['type'] == 'name':
                    continue

                if len(actual['ref']) != 2:
                    continue

                base_name, index = actual['ref']
                if index['ref'] != [for_var_name]:
                    continue

                result[base_name] = result.get(base_name, []) + [statement]

    return result


ASSERT_IMPORT_JA: Final[str] = 'import c_assert(int, string) as assert;'


# TODO handle init/use loops in subs - pass loop bounds as parameters if needed
# TODO handle single uses
# TODO handle init/use conditions
def generate_assertions(src_path: Path, ja_path: Path, ti_path: Path) -> None:
    ja: dict = json.loads(ja_path.read_text())
    ti = TextInfo(json.loads(ti_path.read_text()))

    init_loops = find_init_loops(ja['main'], ja)
    use_loops = find_use_loops(ja['main'], ja)

    source_file_lines: dict[str, list[str]] = {}

    for base_name, name_use_loops in use_loops.items():
        assert len(init_loops[base_name]) == 1
        init_loop = init_loops[base_name][0]

        init_first = init_loop['first']
        init_last = init_loop['last']

        for use_loop in name_use_loops:
            file_name = ti.file_name(use_loop['begin'])
            file_path = src_path / file_name

            if file_name not in source_file_lines:
                source_file_lines[file_name] = file_path.read_text().splitlines()

                source_file_lines[file_name][0] = f'{ASSERT_IMPORT_JA}\n{source_file_lines[file_name][0]}'

            use_first = use_loop['first']
            use_last = use_loop['last']

            # assert_comment = f'// generated assert for {base_name}, file {file_name}'

            assertions_ja = [
                f'{ja_expression_to_str(use_first)} >= {ja_expression_to_str(init_first)}',
                f'{ja_expression_to_str(use_last)} <= {ja_expression_to_str(init_last)}'
            ]
            for assertion_ja in assertions_ja:
                # TODO this only works if each statement starts on a new line
                # TODO cannot use 'begin' to insert assertions because it refers to preprocessed.fa, not the original file
                loop_line_number = ti.line_number(use_loop['begin'])
                loop_line = source_file_lines[file_name][loop_line_number - 1]
                indent = 0
                while loop_line[indent] == ' ':
                    indent += 1

                padding = ' ' * indent
                source_file_lines[file_name][loop_line_number - 1] = (
                    # f'{padding}{assert_comment}\n'
                    f'{padding}assert({assertion_ja}, "{assertion_ja} (use {base_name})");\n'
                    f'{loop_line}'
                )

    src_with_assertions_path = src_path / 'with_assertions'

    for file_name, lines in source_file_lines.items():
        file_path = src_with_assertions_path / file_name
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text('\n'.join(lines))

    shutil.copyfile(src=C_ASSERT_CPP_PATH, dst=src_with_assertions_path / C_ASSERT_CPP_PATH.name)


def main() -> None:
    luna_src_path: Final[Path] = Path() / 'tests' / '01_for' / '01_simple'
    luna_build_path: Final[Path] = luna_src_path / 'build'

    luna_ja_only_path = Path(__file__).parent / 'luna_ja_only' / 'luna'

    try:
        subprocess.run(
            args=[
                'python3',
                str(luna_ja_only_path.absolute()),
                '-q',
                '--compile-only',
                f'--build-dir={str(luna_build_path.name)}',
                *[it.name for it in luna_src_path.iterdir() if it.suffix == '.fa'],
            ],
            cwd=luna_src_path,
            env=os.environ,
            capture_output=True,
            check=True
        )
    except subprocess.CalledProcessError as e:
        print(e.stderr.decode())

    generate_assertions(
        src_path=luna_src_path,
        ja_path=luna_build_path / 'program_recom.ja',
        ti_path=luna_build_path / 'preprocessed.fa.ti'
    )

    shutil.rmtree(luna_build_path)


if __name__ == '__main__':
    main()
