from __future__ import annotations

import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from clang import cindex

from adapt_file_names import C_HASH_HEADER_PATH, C_ASSERT_HEADER_PATH, C_ASSERT_CPP_PATH
from adapt_function_names import HASH_NAME_C, ASSERT_NAME_C

cindex.Config.set_library_file('/usr/lib/llvm-18/lib/libclang.so')  # adjust path


@dataclass(frozen=True)
class Edit:
    position: int
    text: str


class Pragma:
    pass


@dataclass(frozen=True)
class PragmaUserType(Pragma):
    argument_name: str
    type_name: str


class UnknownPragma(Exception):
    def __init__(self, pragma_type: str) -> None:
        self.pragma_type = pragma_type

    def __str__(self) -> str:
        return f'Unknown pragma: "{self.pragma_type}"'


class InvalidPragmaSyntax(Exception):
    def __str__(self) -> str:
        return f'Invalid pragma syntax'


class InvalidUserTypePragmaArgumentName(Exception):
    def __init__(self, argument_name: str, function_name: str) -> None:
        self.argument_name = argument_name
        self.function_name = function_name

    def __str__(self) -> str:
        return f'Function "{self.function_name}" has no argument named "{self.argument_name}"'


def try_parse_pragma(line: str) -> Pragma | None:
    if not re.match(r'\s*#\s*pragma\s+adapt', line):
        return None

    match = re.match(
        r'\s*#\s*pragma\s+adapt\s+(\w+)\s*\(\s*([^)]+)\s*\)',
        line
    )

    if not match:
        raise InvalidPragmaSyntax()

    pragma_type = match.group(1)
    pragma_arguments = [it.strip() for it in match.group(2).strip().split(',')]

    match pragma_type, pragma_arguments:
        case 'user_type', [argument_name, type_name]:
            return PragmaUserType(argument_name=argument_name, type_name=type_name)
        case _:
            raise UnknownPragma(pragma_type)


def find_pragmas_before_line(lines: list[str], line_number: int) -> list[Pragma]:
    result = []

    line_index = line_number - 2

    while line_index >= 0:
        pragma = try_parse_pragma(lines[line_index])
        if not pragma:
            break

        result.insert(0, pragma)
        line_index -= 1

    return result


@dataclass(frozen=True)
class FunctionArgument:
    name: str
    type: str

    @staticmethod
    def from_cursor(cursor: cindex.Cursor) -> FunctionArgument:
        return FunctionArgument(
            name=cursor.spelling,
            type=get_declared_type(cursor)
        )


@dataclass(frozen=True)
class Statement:
    extent: cindex.SourceRange


@dataclass(frozen=True)
class Function:
    name: str
    arguments: list[FunctionArgument]
    is_extern_c: bool

    location: cindex.SourceLocation

    body: Statement | None

    @staticmethod
    def from_cursor(cursor: cindex.Cursor) -> Function:
        body: Statement | None = None
        for child in cursor.get_children():
            if child.kind != cindex.CursorKind.COMPOUND_STMT:
                continue

            body = Statement(extent=child.extent)
            break

        return Function(
            name=(cursor.spelling),
            arguments=[FunctionArgument.from_cursor(arg) for arg in cursor.get_arguments()],
            is_extern_c=is_extern_c(cursor),
            location=cursor.location,
            body=body
        )


def parse_function_pragmas(source_lines: list[str], function: Function) -> list[Pragma]:
    pragmas = find_pragmas_before_line(lines=source_lines, line_number=function.location.line)
    for pragma in pragmas:
        match pragma:
            case PragmaUserType(argument_name, _):
                if not any(arg.name == argument_name for arg in function.arguments):
                    raise InvalidUserTypePragmaArgumentName(
                        argument_name=argument_name,
                        function_name=function.name
                    )
            case _:
                pass
    return pragmas


def get_source_offset(location):
    return location.offset


ARGUMENT_TYPE_INPUT_DF: Final[str] = 'const InputDF &'
ARGUMENT_TYPE_OUTPUT_DF: Final[str] = 'OutputDF &'


def is_input_df_param(param: cindex.Cursor) -> bool:
    return get_declared_type(param) == ARGUMENT_TYPE_INPUT_DF


def is_output_df_param(param: cindex.Cursor) -> bool:
    return get_declared_type(param) == ARGUMENT_TYPE_OUTPUT_DF


def collect_returns(node, returns):
    for child in node.get_children():
        if child.kind == cindex.CursorKind.RETURN_STMT:
            returns.append(child)
        collect_returns(child, returns)


def is_extern_c(cursor: cindex.Cursor) -> bool:
    # TODO In libclang, mangled_name often equals spelling for extern "C"
    if cursor.kind != cindex.CursorKind.FUNCTION_DECL:
        return False

    try:
        mangled = cursor.mangled_name
        if mangled == cursor.spelling:
            return True
    except Exception:
        pass

    return False


def find_extern_c_functions(node: cindex.Cursor, file: str) -> list[cindex.Cursor]:
    result = []

    if str(node.location.file) == file and node.kind == cindex.CursorKind.FUNCTION_DECL and is_extern_c(node):
        result.append(node)

    for child in node.get_children():
        result.extend(find_extern_c_functions(child, file))

    return result


def find_translation_unit_functions(
        source_lines: list[str],
        translation_unit: cindex.TranslationUnit
) -> list[Function]:
    def find_functions(node: cindex.Cursor) -> list[Function]:
        result = []

        if node.kind == cindex.CursorKind.FUNCTION_DECL:
            result.append(Function.from_cursor(cursor=node))

        for child in node.get_children():
            result.extend(find_functions(child))

        return result

    return find_functions(translation_unit.cursor)


def get_declared_type(param: cindex.Cursor) -> str:
    tokens = list(param.get_tokens())

    # drop parameter name (last token in most cases)
    if not tokens:
        return ''

    # heuristic: last token is usually the name
    tokens = tokens[:-1]

    const = False
    ref = ''
    base_tokens = []

    for t in tokens:
        s = t.spelling

        if s == 'const':
            const = True
        elif s in ('&', '&&'):
            ref = s
        else:
            base_tokens.append(s)

    base = ' '.join(base_tokens).strip()

    # canonical form
    result = ''
    if const:
        result += 'const '
    result += base
    if ref:
        result += ' ' + ref

    return ' '.join(result.split())


def generate_hash_call(parameter_name: str) -> str:
    return f'{HASH_NAME_C}({parameter_name}.get_data(), {parameter_name}.get_size())'


def generate_hash_variable_name(parameter_name: str) -> str:
    return f'__adapt_generated_hash_{parameter_name}'


def generate_hash_initialization(parameter_name: str) -> str:
    return f'\n        auto const {generate_hash_variable_name(parameter_name)} = {generate_hash_call(parameter_name)};\n'


def generate_hash_check(file: str, func: str, parameter_name: str) -> str:
    return f'\n        {ASSERT_NAME_C}({generate_hash_call(parameter_name)} == {generate_hash_variable_name(parameter_name)}, ' \
           f'"File {file}, function {func}: input parameter {parameter_name} changed");\n    '


def parse_generate_pragma(line):
    """
    Parse:

        #pragma generate assert_kind(parameter)

    Returns:

        {
            "assert_kind": "assert_kind",
            "parameter": "parameter"
        }

    or None if it does not match.
    """

    m = re.match(
        r'\s*#\s*pragma\s+adapt\s+(\w+)\s*\(\s*([^)]+)\s*\)',
        line
    )

    if not m:
        return None

    return {
        'kind': m.group(1),
        'parameters': [it.strip() for it in m.group(2).strip().split(',')],
    }


def get_function_info(cursor):
    """
    Extract useful information from a FUNCTION_DECL.
    """

    body = None

    for child in cursor.get_children():
        if child.kind == cindex.CursorKind.COMPOUND_STMT:
            body = child
            break

    params = []

    for arg in cursor.get_arguments():
        params.append({
            "name": arg.spelling,
            "type": arg.type.spelling,
            "tokens": [
                t.spelling for t in arg.get_tokens()
            ]
        })

    return {
        "cursor": cursor,
        "name": cursor.spelling,

        "location": {
            "file": str(cursor.location.file),
            "line": cursor.location.line,
            "column": cursor.location.column,
        },

        "parameters": params,

        # Cursor pointing at the body
        "body_cursor": body,

        # Source range of whole function
        "extent": {
            "start": cursor.extent.start.offset,
            "end": cursor.extent.end.offset,
        }
    }


def find_functions_with_pragmas(lines: list[str], cursor: cindex.Cursor, file: str):
    result = []

    def visit(node):
        # print(f'Visiting {node.kind}: {node.spelling}')
        if node.location.file and str(node.location.file) != file:
            return

        if node.kind == cindex.CursorKind.FUNCTION_DECL:
            pragmas = find_pragmas_before_line(
                lines,
                node.location.line
            )

            info = get_function_info(node)

            if pragmas:
                result.append({
                    "pragmas": pragmas,
                    "function": info
                })

        for child in node.get_children():
            visit(child)

    visit(cursor)

    return result


def apply_edits(edits: list[Edit], source: str) -> str:
    def key(e: Edit) -> int:
        return e.position

    for edit in sorted(edits, key=key, reverse=True):
        source = source[:edit.position] + edit.text + source[edit.position:]

    return source


def generate_user_type_string_cpp(type_name: str) -> str:
    return f'"adapt_user_type={type_name};"'


def generate_user_type_assignment_cpp(argument_name: str, type_name: str) -> str:
    return f'\n        {argument_name}.DBG_INFO = {generate_user_type_string_cpp(type_name)} + {argument_name}.DBG_INFO;\n'


def generate_user_type_check_cpp(function_name: str, file: str, argument_name: str, type_name: str) -> str:
    return (f'\n        {ASSERT_NAME_C}('
            f'{argument_name}.DBG_INFO.find({generate_user_type_string_cpp(type_name)}) != std::string::npos, '
            f'"File {file}, function {function_name}: expected {argument_name} type to have user type {type_name}");\n')


def instrument_cpp(cpp_path: Path, instrumented_path: Path) -> None:
    index = cindex.Index.create()
    translation_unit = index.parse(cpp_path, args=[
        '-std=c++11'
    ])

    source = cpp_path.read_text(encoding='utf-8')
    source_lines = source.splitlines()

    functions = [
        function
        for function in find_translation_unit_functions(source_lines, translation_unit)
        if str(function.location.file) == str(cpp_path)
           and function.body is not None
    ]

    edits = [
        Edit(position=0, text=f'#include "{C_HASH_HEADER_PATH.name}"\n'),
        Edit(position=0, text=f'#include "{C_ASSERT_HEADER_PATH.name}"\n'),
    ]

    generated_type_checks_count = 0
    generated_type_assignments_count = 0

    for function in functions:
        body_start = function.body.extent.start.offset

        input_args: dict[str, FunctionArgument] = {
            arg.name: arg
            for arg in function.arguments
            if arg.type == ARGUMENT_TYPE_INPUT_DF
        }
        output_args: dict[str, FunctionArgument] = {
            arg.name: arg
            for arg in function.arguments
            if arg.type == ARGUMENT_TYPE_OUTPUT_DF
        }

        for pragma in parse_function_pragmas(source_lines, function):
            match pragma:
                case PragmaUserType(argument_name, type_name):
                    if argument_name in input_args:
                        edits.append(Edit(
                            position=body_start + 1,
                            text=generate_user_type_check_cpp(
                                file=str(function.location.file),
                                function_name=function.name,
                                argument_name=argument_name,
                                type_name=type_name),
                        ))
                        generated_type_checks_count += 1
                    if argument_name in output_args:
                        edits.append(Edit(
                            position=body_start + 1,
                            text=generate_user_type_assignment_cpp(argument_name=argument_name, type_name=type_name),
                        ))
                        generated_type_assignments_count += 1

    source = apply_edits(edits, source)

    if not instrumented_path.is_absolute():
        instrumented_path = cpp_path.parent / instrumented_path

    if instrumented_path.exists() and not instrumented_path.is_dir():
        raise NotADirectoryError()

    instrumented_path.mkdir(parents=True, exist_ok=True)

    shutil.copyfile(src=C_ASSERT_HEADER_PATH, dst=instrumented_path / C_ASSERT_HEADER_PATH.name)
    shutil.copyfile(src=C_ASSERT_CPP_PATH, dst=instrumented_path / C_ASSERT_CPP_PATH.name)

    dst_cpp_path = instrumented_path / cpp_path.name
    dst_cpp_path.write_text(source, encoding='utf-8')

    print(f'Generated {generated_type_assignments_count} type initializations.')
    print(f'Generated {generated_type_checks_count} type checks.')
    print(f'Type checks inserted into {dst_cpp_path}.')


def main(cpp_path: Path) -> None:
    index = cindex.Index.create()
    translation_unit = index.parse(cpp_path, args=[
        '-std=c++17'
    ])

    source = cpp_path.read_text(encoding='utf-8')

    edits = [
        Edit(pos=0, text=f'#include "{C_HASH_HEADER_PATH.name}"\n'),
        Edit(pos=0, text=f'#include "{C_ASSERT_HEADER_PATH.name}"\n'),
    ]

    funcs = find_extern_c_functions(translation_unit.cursor, file=str(cpp_path))

    generated_hash_checks_count = 0

    for func in funcs:
        input_params = [p for p in func.get_arguments() if is_input_df_param(p)]
        if not input_params:
            continue

        body = function_body_extent(func)
        if not body:
            continue

        body_start = get_source_offset(body.start)
        body_end = get_source_offset(body.end)

        # Insert hashes after '{'
        insert_pos = body_start + 1
        hash_lines = []
        for p in input_params:
            hash_lines.append(generate_hash_initialization(p.spelling))

        edits.append(Edit(insert_pos, "".join(hash_lines)))

        # Insert assertions before returns
        returns = []
        collect_returns(func, returns)

        for ret in returns:
            ret_pos = get_source_offset(ret.extent.start)

            assert_lines = []
            for p in input_params:
                assert_lines.append(generate_hash_check(cpp_path.name, func.spelling, p.spelling))
                generated_hash_checks_count += 1

            edits.append(Edit(ret_pos, ''.join(assert_lines)))

        # Insert at end of function (before closing '}')
        end_asserts = []
        for p in input_params:
            end_asserts.append(generate_hash_check(cpp_path.name, func.spelling, p.spelling))
            generated_hash_checks_count += 1

        edits.append(Edit(body_end - 1, ''.join(end_asserts)))

    source = apply_edits(edits, source)

    c_hash_header_path_dst = cpp_path.parent / C_HASH_HEADER_PATH.name
    shutil.copyfile(src=C_HASH_HEADER_PATH, dst=c_hash_header_path_dst)
    shutil.copyfile(src=C_ASSERT_HEADER_PATH, dst=cpp_path.parent / C_ASSERT_HEADER_PATH.name)
    shutil.copyfile(src=C_ASSERT_CPP_PATH, dst=cpp_path.parent / C_ASSERT_CPP_PATH.name)

    dst_cpp_path = cpp_path.with_name(f'{cpp_path.stem}_instrumented{cpp_path.suffix}')
    dst_cpp_path.write_text(source, encoding='utf-8')
    cpp_path.rename(cpp_path.with_name(f'{cpp_path.name}.bak'))

    print(f'Generated {generated_hash_checks_count} hash checks.')
    print(f'Hash checks inserted into {dst_cpp_path.name}.')


if __name__ == '__main__':
    if len(sys.argv) != 3:
        print('Usage: python instrument_cpp.py file.cpp instrumented_path')
        sys.exit(1)

    instrument_cpp(Path(sys.argv[1]), Path(sys.argv[2]))
