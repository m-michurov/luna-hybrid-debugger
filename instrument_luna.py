import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Final

import click

from adapt_file_paths import C_ASSERT_CPP_PATH
from adapt_function_names import ASSERT_NAME_C, ASSERT_NAME_LUNA


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

    return f'{name}[{", ".join(map(ja_expression_to_str, indices))}]'


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
            raise NotImplementedError(unsupported_type)


def ja_expression_to_str(expr: dict) -> str:
    match expr['type']:
        case 'id':
            return ja_indexed_expression_to_str(expr)
        case 'iconst':
            return ja_arith_expression_to_str(expr)
        case 'icast':
            return f'int({ja_expression_to_str(expr["expr"])})'
        case op if op in JA_BINARY_OPERATORS or op in JA_UNARY_OPERATORS:
            return ja_arith_expression_to_str(expr)
        case unsupported_type:
            raise NotImplementedError(unsupported_type)


def print_statement(statement: dict, indent: int = 0) -> None:
    print(f'{" " * indent}{statement["type"]}')

    indent += 1
    padding = ' ' * indent

    match statement['type']:
        case 'dfs':
            print(f'{padding}names=[{", ".join(statement["names"])}]')
        case 'exec':
            print(f'{padding}code={statement["code"]}')
            print(f'{padding}args=[{", ".join(map(ja_expression_to_str, statement["args"]))}]')
        case 'for':
            print(f'{padding}var={statement["var"]}')
            print(f'{padding}first={ja_expression_to_str(statement["first"])}')
            print(f'{padding}last={ja_expression_to_str(statement["last"])}')

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


def refs_in_operands(expr: dict) -> list[list]:
    operands = expr.get('operands', [])
    result = []

    for operand in operands:
        if ref := operand.get('ref'):
            result.append(ref)

        result += refs_in_operands(operand)

    return result


def find_use_loops(sub: dict, program: dict) -> dict[str, list[tuple[list, dict]]]:
    """
    :return: {DF_NAME: [(BODY_CONTAINING_FOR, FOR), ...], ...}
    """
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

                if 'operands' in actual:
                    refs = refs_in_operands(actual)
                elif 'ref' in actual:
                    refs = [actual['ref']]
                else:
                    refs = []

                for ref in refs:
                    if len(ref) != 2:
                        continue

                    base_name, index = ref
                    if index['ref'] != [for_var_name]:
                        continue

                    result[base_name] = result.get(base_name, []) + [(sub['body'], statement)]

    return result


def find_conditional_single_inits(sub: dict, program: dict) -> dict[str, list[tuple[list, dict]]]:
    result: dict[str, list[tuple[list, dict]]] = {}

    for statement in sub['body']:
        if statement['type'] != 'if':
            continue

        for body_statement in statement['body']:
            # TODO handle initializations in while loops
            if body_statement['type'] != 'exec':
                continue

            code_fragment_name = body_statement['code']
            code_fragment = program[code_fragment_name]

            for formal, actual in zip(code_fragment['args'], body_statement['args']):
                if formal['type'] != 'name':
                    continue

                ref = ja_expression_to_str(actual)
                result[ref] = result.get(ref, []) + [(sub['body'], statement)]

    return result


def find_single_uses(sub: dict, program: dict) -> dict[str, list[list]]:
    result: dict[str, list[list]] = {}

    for statement in sub['body']:
        if statement['type'] == 'exec':
            code_fragment_name = statement['code']
            code_fragment = program[code_fragment_name]

            for formal, actual in zip(code_fragment['args'], statement['args']):
                if formal['type'] == 'name':
                    continue

                if actual['type'] != 'id':
                    continue

                ref = ja_expression_to_str(actual)
                result[ref] = result.get(ref, []) + [sub['body']]

    return result


def is_error_loop_in_main(error_loop: dict) -> bool:
    error_loop_where = error_loop['where']

    if len(error_loop_where) != 2:
        return False

    if error_loop_where[0]['name'] != 'main' or error_loop_where[1]['name'] != 'for':
        return False

    return True


def find_matching_program_loop(
        error_loop: dict,
        name: str,
        program_loops: dict[str, list[tuple[list, dict]]],
        ti: TextInfo
) -> tuple[list, dict] | None:
    error_loop_where = error_loop['where']
    error_loop_line = error_loop_where[1]['line']

    for body, init_loop in program_loops[name]:
        line = ti.line_number(init_loop['begin'])
        if line != error_loop_line:
            continue

        return body, init_loop


# TODO handle init/use loops in subs - pass loop bounds as parameters if needed
# TODO handle single uses
# TODO handle init/use conditions
# TODO eliminate assertions that can be resolved statically
def generate_loop_assertions(
        ja: dict,
        ti: TextInfo,
        found_errors: list[dict] | None = None,
) -> tuple[int, dict]:
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

    init_loops: dict[str, list[tuple[list, dict]]] = find_init_loops(ja['main'], ja)
    print(
        f'Found {sum(len(it) for it in init_loops.values())} init loops in main ({", ".join(init_loops.keys())}).')
    use_loops: dict[str, list[tuple[list, dict]]] = find_use_loops(ja['main'], ja)
    print(
        f'Found {sum(len(it) for it in use_loops.values())} use loops in main ({", ".join(use_loops.keys())}).')

    # Filter use loops to only include those mentioned in SEM3.3 errors
    if found_errors is not None:
        sem3_3_errors = [error for error in found_errors if error['error_code'].lower() == 'sem3.3']
        print(f'Will only check loops involved in {len(sem3_3_errors)} SEM3.3 errors.')
        use_loops2 = {}
        init_loops2 = {}
        # TODO conditions are ignored
        # TODO multiple/non-loop initializations are ignored
        # TODO report ignored errors?
        for sem3_3_error in sem3_3_errors:
            initialized = sem3_3_error['details']['initialized']
            used = sem3_3_error['details']['used']
            name = used['df']['df']['name']

            if name not in init_loops or name not in use_loops:
                continue

            if len(initialized) != 1 or 'loop' not in initialized[0]:
                continue

            error_init_loop = initialized[0]['loop']
            if not is_error_loop_in_main(error_init_loop):
                continue

            if (program_init_loop := find_matching_program_loop(error_init_loop, name, init_loops, ti)) is not None:
                init_loops2[name] = init_loops2.get(name, []) + [program_init_loop]

            error_use_loop = used['loop']
            if not is_error_loop_in_main(error_use_loop):
                continue

            if (program_use_loop := find_matching_program_loop(error_use_loop, name, use_loops, ti)) is not None:
                use_loops2[name] = use_loops2.get(name, []) + [program_use_loop]

        use_loops = use_loops2
        del use_loops2
        init_loops = init_loops2
        del init_loops2

    print(
        f'Will check {sum(len(it) for it in init_loops.values())} init loops ({", ".join(init_loops.keys())}).')
    print(
        f'Will check {sum(len(it) for it in use_loops.values())} use loops ({", ".join(use_loops.keys())}).')

    for base_name, name_use_loops in use_loops.items():
        assert len(name_use_loops) > 0
        assert len(init_loops[base_name]) > 0
        assert len(init_loops[base_name]) == 1, f'{base_name} has {len(init_loops[base_name])} init loops'
        _, init_loop = init_loops[base_name][0]

        init_first = init_loop['first']
        init_last = init_loop['last']

        for body, use_loop in name_use_loops:
            use_loop_position = use_loop['begin']

            use_first = use_loop['first']
            use_last = use_loop['last']

            bounds_check_ja = {
                'is_expr': True,
                'type': '&&',
                'operands': [
                    {
                        'is_expr': True,
                        'type': '<=',
                        'operands': [init_first, use_first],
                        'op_pos': 0
                    },
                    {
                        'is_expr': True,
                        'type': '<=',
                        'operands': [use_last, init_last],
                        'op_pos': 0
                    },
                ],
                'op_pos': 0
            }

            error_message = '\n'.join([
                'error_code=SEM##',
                f'base_name={base_name}',
                f'where={ti.file_name(use_loop_position)}:{ti.line_number(use_loop_position)} for',
                f'init_first={ja_expression_to_str(init_first)}',
                f'init_last={ja_expression_to_str(init_last)}',
                f'use_first={ja_expression_to_str(use_first)}',
                f'use_last={ja_expression_to_str(use_last)}',
            ])

            asserts_ja = [
                {
                    'type': 'exec',
                    'code': ASSERT_NAME_LUNA,
                    'id': ['_l0'],
                    'args': [
                        bounds_check_ja,
                        {
                            'is_expr': True,
                            'type': 'sconst',
                            'value': error_message
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


def generate_init_condition_assertions(ja: dict, ti: TextInfo) -> tuple[int, dict]:
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

    conditional_single_inits = find_conditional_single_inits(ja['main'], ja)
    single_uses = find_single_uses(ja['main'], ja)

    for name, use_bodies in single_uses.items():
        if name not in conditional_single_inits:
            continue

        assert len(conditional_single_inits[name]) <= 1
        _, single_init_condition = conditional_single_inits[name][0]
        assert single_init_condition['type'] == 'if'

        if_position = single_init_condition['begin']
        init_condition = single_init_condition['cond']

        for body in use_bodies:
            error_message = '\n'.join([
                'error_code=SEM##',
                f'name={name}',
                f'where={ti.file_name(if_position)}:{ti.line_number(if_position)} if',
                f'condition={ja_expression_to_str(init_condition)}',
            ])

            asserts_ja = [
                {
                    'type': 'exec',
                    'code': ASSERT_NAME_LUNA,
                    'id': ['_l0'],
                    'args': [
                        init_condition,
                        {
                            'is_expr': True,
                            'type': 'sconst',
                            'value': error_message
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
@click.option(
    '--build-dir',
    required=False,
    show_default=True,
    default=Path('build'),
    help='Directory for build files.',
    type=click.Path(
        file_okay=False,
        resolve_path=True,
        path_type=Path,
    )
)
@click.option(
    '--output-dir',
    required=False,
    show_default=True,
    default=Path('instrumented'),
    help='Directory for program output.',
    type=click.Path(
        file_okay=False,
        resolve_path=True,
        path_type=Path,
    )
)
@click.option(
    '--found-errors',
    required=False,
    help='Use found_errors.json file generated by other ADAPT modules to insert only necessary checks.',
    type=click.Path(
        file_okay=True,
        resolve_path=True,
        path_type=Path,
    )
)
def main(
        project_path: Path,
        build_dir: Path,
        output_dir: Path,
        found_errors: Path | None = None,
) -> None:
    run(
        project_path=project_path,
        build_dir=build_dir,
        output_dir=output_dir,
        found_errors=found_errors,
    )


def run(
        project_path: Path,
        build_dir: Path,
        output_dir: Path,
        found_errors: Path | None = None,
        standalone: bool = True
) -> None:
    luna_ja_only_path = Path(__file__).parent.joinpath('luna_ja_only', 'luna').resolve()
    luna_after_ja_path = Path(__file__).parent.joinpath('luna_after_ja', 'luna').resolve()

    try:
        subprocess.run(
            args=[
                'python3',
                str(luna_ja_only_path),
                '-q',
                '--compile-only',
                f'--build-dir={str(build_dir)}',
                *[it.name for it in project_path.iterdir() if it.suffix == '.fa'],
            ],
            cwd=project_path,
            capture_output=True,
            check=True
        )
    except subprocess.CalledProcessError as e:
        print(e.stderr.decode(), file=sys.stderr)
        sys.exit(1)

    if found_errors.exists():
        print(f'Attempting to use {found_errors} to only generate necessary checks.')

    text_info = TextInfo(json.loads(build_dir.joinpath('preprocessed.fa.ti').read_text(encoding='utf-8')))

    loop_assertions_count, ja_with_assertions = generate_loop_assertions(
        ja=json.loads(build_dir.joinpath('program_foreign.ja').read_text(encoding='utf-8')),
        ti=text_info,
        found_errors=json.loads(found_errors.read_text(encoding='utf-8')) if found_errors.exists() else None,
    )
    condition_assertions_count, ja_with_assertions = generate_init_condition_assertions(
        ja=ja_with_assertions,
        ti=text_info
    )

    assertions_count = loop_assertions_count + condition_assertions_count
    print(f'Generated {assertions_count} assertion(s).')
    if assertions_count <= 0:
        print('Program was not modified.')
        if standalone:
            return

    if standalone:
        shutil.copytree(
            src=project_path,
            dst=output_dir,
            ignore=lambda _1, _2: [output_dir.name],
            dirs_exist_ok=True
        )
        shutil.copyfile(src=C_ASSERT_CPP_PATH, dst=output_dir / C_ASSERT_CPP_PATH.name)

    build_dir.joinpath('program_foreign.ja').rename(build_dir.joinpath('program_foreign.bak.ja'))
    build_dir.joinpath('program_foreign.ja') \
        .write_text(json.dumps(ja_with_assertions, indent=2), encoding='utf-8')

    fa_paths = [it.resolve() for it in project_path.iterdir() if it.suffix == '.fa']
    for path in fa_paths:
        shutil.copyfile(src=path, dst=output_dir / path.name)

    if not standalone:
        return

    print('Run modified program with:')
    print(f'cd {output_dir} ;', end=' ')
    print(f'rm -f .adapt_error_*.dat ;', end=' ')
    print(
        f'{luna_after_ja_path.resolve()}',
        f'--build-dir={str(build_dir)}',
        '--no-cleanup',
        *(str(it.resolve()) for it in output_dir.iterdir() if it.suffix == '.fa'),
        ';',
        sep=' ',
        end=' '
    )
    print(f'cd -')


if __name__ == '__main__':
    main()
