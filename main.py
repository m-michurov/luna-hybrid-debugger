import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Final

C_ASSERT_CPP_PATH: Final[Path] = Path(__file__).parent / '_c_generated_assert.cpp'


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


def find_init_loops(sub: dict, program: dict) -> dict[str, list[tuple[list, dict]]]:
    result: dict[str, list[tuple[list, dict]]] = {}

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

                result[base_name] = result.get(base_name, []) + [(sub['body'], statement)]

    return result


def find_use_loops(sub: dict, program: dict) -> dict[str, list[tuple[list, dict]]]:
    result: dict[str, list[tuple[list, dict]]] = {}

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

                result[base_name] = result.get(base_name, []) + [(sub['body'], statement)]

    return result

ASSERT_NAME_C: Final[str] = '_c_generated_assert'
ASSERT_NAME_LUNA: Final[str] = '_generated_assert'

ASSERT_IMPORT_JA: Final[str] = f'import {ASSERT_NAME_C}(int, string) as {ASSERT_NAME_LUNA};'


# TODO handle init/use loops in subs - pass loop bounds as parameters if needed
# TODO handle single uses
# TODO handle init/use conditions
# TODO eliminate assertions that can be resolved statically
def generate_assertions(src_path: Path, ja: dict, ti: TextInfo) -> tuple[int, dict]:
    assertions_count = 0

    ja_with_assertions = ja
    ja_with_assertions[ASSERT_NAME_LUNA] = {
        'args': [
            {
                'type': 'int'
            },
            {
                'type': 'string'
            }
        ],
        'code': ASSERT_NAME_C,
        'code_type': 'default',
        'rules': [],
        'type': 'extern'
    }

    init_loops = find_init_loops(ja['main'], ja)
    use_loops = find_use_loops(ja['main'], ja)

    source_file_lines: dict[str, list[str]] = {}

    for base_name, name_use_loops in use_loops.items():
        assert len(init_loops[base_name]) == 1
        _, init_loop = init_loops[base_name][0]

        init_first = init_loop['first']
        init_last = init_loop['last']

        for body, use_loop in name_use_loops:
            file_name = ti.file_name(use_loop['begin'])
            file_path = src_path / file_name

            if file_name not in source_file_lines:
                source_file_lines[file_name] = file_path.read_text().splitlines()

                source_file_lines[file_name][0] = f'{ASSERT_IMPORT_JA}\n{source_file_lines[file_name][0]}'

            use_first = use_loop['first']
            use_last = use_loop['last']

            # TODO single assert
            asserts_ja = [
                {
                    'type': 'exec',
                    'code': ASSERT_NAME_LUNA,
                    'id': ['_l0'],
                    'args': [
                        {
                            'is_expr': True,
                            'type': '<=',
                            'operands': [init_first, use_first],
                            'op_pos': 0
                        },
                        {
                            'is_expr': True,
                            'type': 'sconst',
                            'value': f'{ja_expression_to_str(init_first)} <= {ja_expression_to_str(use_first)} use {base_name})'
                        }
                    ],
                    'rules': [],
                    'begin': 0
                },
                {
                    'type': 'exec',
                    'code': ASSERT_NAME_LUNA,
                    'id': ['_l0'],
                    'args': [
                        {
                            'is_expr': True,
                            'type': '<=',
                            'operands': [use_last, init_last],
                            'op_pos': 0
                        },
                        {
                            'is_expr': True,
                            'type': 'sconst',
                            'value': f'{ja_expression_to_str(use_last)} <= {ja_expression_to_str(init_last)} use {base_name})'
                        }
                    ],
                    'rules': [],
                    'begin': 0
                },
            ]
            for assert_ja in asserts_ja:
                body.append(assert_ja)
                assertions_count += 1

    return assertions_count, ja_with_assertions


def main() -> None:
    # TODO make this example work
    luna_src_path: Final[Path] = Path() / 'tests' / '01_for' / '01_simple'
    luna_build_path: Final[Path] = luna_src_path / 'build'

    luna_ja_only_path = Path(__file__).parent / 'luna_ja_only' / 'luna'
    luna_after_ja_path = Path(__file__).parent / 'luna_after_ja' / 'luna'

    try:
        subprocess.run(
            args=[
                'python3',
                str(luna_ja_only_path.absolute()),
                '-q',
                '--compile-only',
                f'--build-dir={str(luna_build_path.absolute())}',
                *[it.name for it in luna_src_path.iterdir() if it.suffix == '.fa'],
            ],
            cwd=luna_src_path,
            capture_output=True,
            check=True
        )
    except subprocess.CalledProcessError as e:
        print(e.stderr.decode(), file=sys.stderr)
        sys.exit(1)

    assertions_count, ja_with_assertions = generate_assertions(
        src_path=luna_src_path,
        ja=json.loads(luna_build_path.joinpath('program_foreign.ja').read_text(encoding='utf-8')),
        ti=TextInfo(json.loads(luna_build_path.joinpath('preprocessed.fa.ti').read_text(encoding='utf-8'))),
    )

    print(f'Generated {assertions_count} assertion(s).')
    if assertions_count <= 0:
        print('Program was not modified.')
        return

    shutil.copyfile(src=C_ASSERT_CPP_PATH, dst=luna_src_path / C_ASSERT_CPP_PATH.name)

    luna_build_path.joinpath('program_foreign.ja').rename(luna_build_path.joinpath('program_foreign.bak.ja'))
    luna_build_path.joinpath('program_foreign.ja') \
        .write_text(json.dumps(ja_with_assertions, indent=2), encoding='utf-8')

    print('Run modified program with:')
    print(
        f'{luna_after_ja_path.absolute()}',
        f'--build-dir={str(luna_build_path.absolute())}',
        '--no-cleanup',
        *(str(it.absolute()) for it in luna_src_path.iterdir() if it.suffix == '.fa'),
        sep=' '
    )


if __name__ == '__main__':
    main()
