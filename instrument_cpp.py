import shutil
import sys
from pathlib import Path
from typing import Final

from clang import cindex

from adapt_file_names import C_HASH_HEADER_PATH, C_ASSERT_HEADER_PATH, C_ASSERT_CPP_PATH
from adapt_function_names import HASH_NAME_C, ASSERT_NAME_C

cindex.Config.set_library_file("/usr/lib/llvm-18/lib/libclang.so")  # adjust path


class Edit:
    def __init__(self, pos, text):
        self.pos = pos
        self.text = text


def get_source_offset(location):
    return location.offset


def get_source_text(start, end, source):
    return source[start:end]


DECLARED_INPUT_DF_PARAMETER_TYPE: Final[str] = 'const InputDF &'


def is_input_df_param(param: cindex.Cursor) -> bool:
    return get_declared_type(param) == DECLARED_INPUT_DF_PARAMETER_TYPE


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


def function_body_extent(func):
    for child in func.get_children():
        if child.kind == cindex.CursorKind.COMPOUND_STMT:
            return child.extent
    return None


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

    for edit in sorted(edits, key=lambda it: it.pos, reverse=True):
        source = source[:edit.pos] + edit.text + source[edit.pos:]

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
    if len(sys.argv) != 2:
        print('Usage: python instrument_cpp.py file.cpp')
        sys.exit(1)

    main(Path(sys.argv[1]))
