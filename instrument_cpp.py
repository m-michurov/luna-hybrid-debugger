from __future__ import annotations

import dataclasses
import re
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Final

from clang import cindex

from adapt_file_paths import C_HASH_HEADER_PATH, C_ASSERT_HEADER_PATH, C_ASSERT_CPP_PATH
from adapt_function_names import HASH_NAME_C, ASSERT_NAME_C

cindex.Config.set_library_file('/usr/lib/llvm-18/lib/libclang.so')  # adjust path


@dataclass(frozen=True)
class Edit:
    position: int
    text: str

    @staticmethod
    def from_lines(position: int, lines: list[str], indent: int | str) -> Edit:
        if type(indent) is int:
            indent = ' ' * indent

        return Edit(position=position, text=f'\n{indent}'.join(['', *lines]) + '\n')


class Pragma:
    pass


class PragmaName(StrEnum):
    USER_TYPE = 'user_type'
    SERIALIZED_SIZE = 'serialized_size'
    SERIALIZE = 'serialize'
    DESERIALIZE = 'deserialize'


@dataclass(frozen=True)
class PragmaUserType(Pragma):
    argument_name: str
    type_name: str


@dataclass(frozen=True)
class PragmaSerializedSize(Pragma):
    type_name: str


@dataclass(frozen=True)
class PragmaSerialize(Pragma):
    type_name: str


@dataclass(frozen=True)
class PragmaDeserialize(Pragma):
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


class InvalidSignature(Exception):
    def __init__(self, function_name: str, expected_signature: str) -> None:
        self.function_name = function_name
        self.expected_signature = expected_signature

    def __str__(self) -> str:
        return f'Function "{self.function_name}" must have signature {self.expected_signature}'


class MissingPragmaForType(Exception):
    def __init__(self, pragma_name: str, type_name: str) -> None:
        self.pragma_name = pragma_name
        self.type_name = type_name

    def __str__(self) -> str:
        return f'No function annotated with "{self.pragma_name}" for type "{self.type_name}"'


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
        case PragmaName.USER_TYPE, [argument_name, type_name]:
            return PragmaUserType(argument_name=argument_name, type_name=type_name)
        case PragmaName.SERIALIZED_SIZE, [type_name]:
            return PragmaSerializedSize(type_name=type_name)
        case PragmaName.SERIALIZE, [type_name]:
            return PragmaSerialize(type_name=type_name)
        case PragmaName.DESERIALIZE, [type_name]:
            return PragmaDeserialize(type_name=type_name)
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


@dataclass(frozen=True, unsafe_hash=True)
class Function:
    name: str
    arguments: tuple[FunctionArgument, ...]
    return_type: str
    is_extern_c: bool

    location: cindex.SourceLocation = field(hash=False)

    body: Statement | None = field(hash=False)

    @staticmethod
    def from_cursor(cursor: cindex.Cursor) -> Function:
        body: Statement | None = None
        for child in cursor.get_children():
            if child.kind != cindex.CursorKind.COMPOUND_STMT:
                continue

            body = Statement(extent=child.extent)
            break

        return Function(
            name=cursor.spelling,
            arguments=tuple(FunctionArgument.from_cursor(arg) for arg in cursor.get_arguments()),
            return_type=cursor.result_type.spelling,
            is_extern_c=is_function_extern_c(cursor),
            location=cursor.location,
            body=body
        )


def parse_function_pragmas(source_lines: list[str], function: Function) -> list[Pragma]:
    pragmas = find_pragmas_before_line(lines=source_lines, line_number=function.location.line)

    # Validate
    for pragma in pragmas:
        match pragma:
            case PragmaUserType(argument_name, _):
                if not any(arg.name == argument_name for arg in function.arguments):
                    raise InvalidUserTypePragmaArgumentName(
                        argument_name=argument_name,
                        function_name=function.name
                    )
            case PragmaSerializedSize(type_name):
                if len(function.arguments) == 1 and function.arguments[0].type == type_name:
                    continue

                raise InvalidSignature(function_name=function.name, expected_signature=f'({type_name})')
            case PragmaSerialize(type_name):
                if len(function.arguments) == 2:
                    arg1, arg2 = function.arguments

                    if arg1.type == type_name and arg2.type == 'void *':
                        continue

                raise InvalidSignature(function_name=function.name, expected_signature=f'({type_name}, void *)')
            case PragmaDeserialize(type_name):
                if len(function.arguments) == 2:
                    arg1, arg2 = function.arguments

                    if arg1.type == f'{type_name} &' and arg2.type == 'const void *':
                        continue

                raise InvalidSignature(function_name=function.name, expected_signature=f'({type_name} &, const void *)')
            case _:
                pass

    return pragmas


def generate_signature_cpp(function: Function) -> str:
    return f'{function.return_type} {function.name}({', '.join([f'{argument.type} {argument.name}' for argument in function.arguments])}'')'


def generate_forward_declaration_cpp(function: Function) -> str:
    return f'{generate_signature_cpp(function)};'


ARGUMENT_TYPE_INPUT_DF: Final[str] = 'const InputDF &'
ARGUMENT_TYPE_OUTPUT_DF: Final[str] = 'OutputDF &'


def collect_returns(node, returns):
    for child in node.get_children():
        if child.kind == cindex.CursorKind.RETURN_STMT:
            returns.append(child)
        collect_returns(child, returns)


def is_function_extern_c(cursor: cindex.Cursor) -> bool:
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


def find_translation_unit_functions(translation_unit: cindex.TranslationUnit) -> list[Function]:
    def find_functions(node: cindex.Cursor) -> list[Function]:
        result = []

        if node.kind == cindex.CursorKind.FUNCTION_DECL:
            result.append(Function.from_cursor(cursor=node))

        for child in node.get_children():
            result.extend(find_functions(child))

        return result

    return find_functions(translation_unit.cursor)


def get_declared_type(cursor: cindex.Cursor) -> str:
    tokens = list(cursor.get_tokens())

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


def generate_hash_call_cpp(parameter_name: str) -> str:
    return f'{HASH_NAME_C}({parameter_name}.get_data(), {parameter_name}.get_size())'


def generate_hash_variable_name_cpp(parameter_name: str) -> str:
    return f'__adapt_hash_{parameter_name}'


def generate_hash_initialization_cpp(argument_name: str) -> str:
    return f'auto const {generate_hash_variable_name_cpp(argument_name)} = {generate_hash_call_cpp(argument_name)};'


def generate_hash_check_cpp(file: str, function_name: str, argument_name: str) -> str:
    return (
        f'{ASSERT_NAME_C}({generate_hash_call_cpp(argument_name)} == {generate_hash_variable_name_cpp(argument_name)}, '
        f'"File {file}, function {function_name}: input parameter {argument_name} changed");')


def apply_edits(source: str, edits: list[Edit]) -> str:
    def key(e: Edit) -> int:
        return e.position

    for edit in sorted(edits, key=key, reverse=True):
        source = source[:edit.position] + edit.text + source[edit.position:]

    return source


def generate_user_type_string_cpp(type_name: str) -> str:
    return f'"adapt_user_type={type_name};"'


def generate_user_type_assignment_cpp(argument_name: str, type_name: str) -> str:
    return f'{argument_name}.DBG_INFO = {generate_user_type_string_cpp(type_name)} + {argument_name}.DBG_INFO;'


def generate_user_type_check_cpp(function_name: str, file: str, argument_name: str, type_name: str) -> str:
    return (f'{ASSERT_NAME_C}('
            f'{argument_name}.DBG_INFO.find({generate_user_type_string_cpp(type_name)}) != std::string::npos, '
            f'"File {file}, function {function_name}: expected {argument_name} type to have user type {type_name}");')


def generate_copy_of_function_cpp(function: Function, source: str) -> str:
    return (f'{generate_signature_cpp(function)} '
            + source[function.body.extent.start.offset:function.body.extent.end.offset])


def generate_deserialization_check_cpp(
        function_name: str,
        file: str,
        serialized_parameter_name: str,
        serialized_parameter_type: str,
        buffer_parameter_name: str,
        serialized_size_function_name: str,
        serialize_function_name: str,
        deserialize_function_name: str,
) -> list[str]:
    size_variable_name = '_size'
    buffer_variable_name = '_buf'
    deserialized_parameter_name = '_deserialized'
    return [
        '{',
        f'    auto {size_variable_name} = {serialized_size_function_name}({serialized_parameter_name});',
        f'    {serialized_parameter_type} {deserialized_parameter_name};',
        f'    {deserialize_function_name}({deserialized_parameter_name}, {buffer_parameter_name});',
        f'    std::vector<uint8_t> {buffer_variable_name}({size_variable_name});',
        f'    {serialize_function_name}({deserialized_parameter_name}, {buffer_variable_name}.data());',
        f'    {ASSERT_NAME_C}(0 == memcmp({buffer_variable_name}.data(), {buffer_parameter_name}, {size_variable_name}), "File {file}, function {function_name}: serialized buffers differ after deserialization");',
        '}'
    ]


def get_source_indentation(source: str, location: cindex.SourceLocation) -> int:
    indent = 0
    char_index = location.offset - 2
    while char_index >= 0:
        match source[char_index]:
            case ' ':
                indent += 1
            case '\t':
                indent += 4
            case _:
                break

        char_index -= 1

    return indent


def get_pragma(pragmas: list[Pragma], predicate: Callable[[Pragma], bool]) -> Pragma | None:
    return next((pragma for pragma in pragmas if predicate(pragma)), None)


def get_function_with_pragma(
        functions: list[Function],
        pragmas: dict[Function, list[Pragma]],
        predicate: Callable[[Pragma], bool]
) -> Function | None:
    for function in functions:
        if get_pragma(pragmas[function], predicate):
            return function

    return None


def instrument_cpp(cpp_path: Path, instrumented_path: Path) -> None:
    index = cindex.Index.create()
    translation_unit = index.parse(cpp_path, args=[
        '-std=c++11'
    ])

    source = cpp_path.read_text(encoding='utf-8')
    source_lines = source.splitlines()

    functions = [
        function
        for function in find_translation_unit_functions(translation_unit)
        if str(function.location.file) == str(cpp_path)
           and function.body is not None
    ]

    edits = [
        Edit(position=0, text=f'#include <cstdint>\n'),
        Edit(position=0, text=f'#include <cstddef>\n'),
        Edit(position=0, text=f'#include <vector>\n'),
        Edit(position=0, text=f'#include "{C_HASH_HEADER_PATH.name}"\n'),
        Edit(position=0, text=f'#include "{C_ASSERT_HEADER_PATH.name}"\n'),
    ]

    generated_type_checks_count = 0
    generated_type_assignments_count = 0
    generated_hash_checks_count = 0
    generated_hash_initializations_count = 0
    generated_deserialization_checks_count = 0

    pragmas: dict[Function, list[Pragma]] = {
        function: parse_function_pragmas(source_lines, function)
        for function in functions
    }

    for function in functions:
        closing_bracket_indent = get_source_indentation(source, function.body.extent.end)
        generated_indent = 4 + closing_bracket_indent

        file = str(function.location.file)

        # Pragma-controlled code generation
        body_start = function.body.extent.start.offset
        body_end = function.body.extent.end.offset - closing_bracket_indent

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

        type_checks = []
        type_assignments = []

        for pragma in pragmas[function]:
            match pragma:
                # User type assertions
                case PragmaUserType(argument_name, type_name):
                    if argument_name in input_args:
                        type_checks.append(generate_user_type_check_cpp(
                            file=file,
                            function_name=function.name,
                            argument_name=argument_name,
                            type_name=type_name
                        ))
                    if argument_name in output_args:
                        type_assignments.append(generate_user_type_assignment_cpp(
                            argument_name=argument_name,
                            type_name=type_name
                        ))

                # Serialization assertions
                case PragmaSerialize(type_name):
                    serialized_size_function = get_function_with_pragma(
                        functions, pragmas,
                        lambda p: isinstance(p, PragmaSerializedSize) and p.type_name == type_name
                    )
                    if not serialized_size_function:
                        raise MissingPragmaForType(pragma_name=PragmaName.SERIALIZED_SIZE, type_name=type_name)

                    deserialize_function = get_function_with_pragma(
                        functions, pragmas,
                        lambda p: isinstance(p, PragmaDeserialize) and p.type_name == type_name
                    )
                    if not deserialize_function:
                        raise MissingPragmaForType(pragma_name=PragmaName.DESERIALIZE, type_name=type_name)

                    serialize_function_nonrec_name = f'_adapt_{function.name}_nonrec'
                    serialize_function_nonrec = dataclasses.replace(function, name=serialize_function_nonrec_name)

                    edits.append(Edit.from_lines(
                        position=body_end - 1,
                        lines=generate_deserialization_check_cpp(
                            file=file,
                            function_name=function.name,
                            serialized_parameter_name=function.arguments[0].name,
                            serialized_parameter_type=function.arguments[0].type,
                            buffer_parameter_name=function.arguments[1].name,
                            serialized_size_function_name=serialized_size_function.name,
                            serialize_function_name=serialize_function_nonrec_name,
                            deserialize_function_name=deserialize_function.name,
                        ),
                        indent=generated_indent
                    ))

                    edits.append(Edit.from_lines(
                        position=body_end - 1,
                        lines=[
                            generate_forward_declaration_cpp(serialize_function_nonrec),
                            generate_forward_declaration_cpp(serialized_size_function),
                            generate_forward_declaration_cpp(deserialize_function)
                        ],
                        indent=generated_indent
                    ))

                    edits.append(Edit(
                        position=body_end,
                        text=f'\n{generate_copy_of_function_cpp(serialize_function_nonrec, source)}'
                    ))

                    generated_deserialization_checks_count += 1

        generated_type_checks_count += len(type_checks)
        generated_type_assignments_count += len(type_assignments)

        if type_checks:
            edits.append(Edit.from_lines(position=body_start + 1, lines=type_checks, indent=generated_indent))
        if type_assignments:
            edits.append(Edit.from_lines(position=body_start + 1, lines=type_assignments, indent=generated_indent))

        # Unconditional hash checks
        hash_initializations = []
        hash_checks = []
        for argument_name in input_args:
            hash_initializations.append(generate_hash_initialization_cpp(argument_name))
            hash_checks.append(generate_hash_check_cpp(
                file=file,
                function_name=function.name,
                argument_name=argument_name
            ))

        generated_hash_checks_count += len(hash_checks)
        generated_hash_initializations_count += len(hash_initializations)

        if hash_initializations:
            edits.append(Edit.from_lines(position=body_start + 1, lines=hash_initializations, indent=generated_indent))
        if hash_checks:
            edits.append(Edit.from_lines(position=body_end - 1, lines=hash_checks, indent=generated_indent))

    print(f'Applying {len(edits)} edits')

    source = apply_edits(source, edits)

    dst_cpp_path = instrumented_path / cpp_path.name
    dst_cpp_path.write_text(source, encoding='utf-8')

    print(f'Generated {generated_type_assignments_count} type initializations.')
    print(f'Generated {generated_type_checks_count} type checks.')
    print(f'Generated {generated_hash_initializations_count} hash initializations.')
    print(f'Generated {generated_hash_checks_count} hash checks.')
    print(f'Generated {generated_deserialization_checks_count} deserialization checks.')
    print(f'See {dst_cpp_path}.')

    if generated_type_checks_count > 0:
        print(f'Type checks generated, debug mode (luna -g) required to run instrumented program.')


def main() -> None:
    match sys.argv:
        case [_, cpp_path_str, instrumented_path_str]:
            pass
        case [_, cpp_path_str]:
            instrumented_path_str = 'instrumented'
        case _:
            print('Usage: python instrument_cpp.py file.cpp instrumented_path')
            sys.exit(1)

    cpp_path = Path(cpp_path_str)
    instrumented_path = Path(instrumented_path_str)

    if not instrumented_path.is_absolute():
        instrumented_path = cpp_path.parent / instrumented_path
    if instrumented_path.exists() and not instrumented_path.is_dir():
        raise NotADirectoryError()
    instrumented_path.mkdir(parents=True, exist_ok=True)

    project_folder = cpp_path.parent
    shutil.copytree(
        src=project_folder,
        dst=instrumented_path,
        ignore=lambda _1, _2: [instrumented_path.name],
        dirs_exist_ok=True
    )
    for path in (C_HASH_HEADER_PATH, C_ASSERT_HEADER_PATH, C_ASSERT_CPP_PATH):
        shutil.copyfile(src=path, dst=instrumented_path / path.name)

    instrument_cpp(cpp_path, instrumented_path)


if __name__ == '__main__':
    main()
