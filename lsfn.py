import sys
from clang import cindex

# Adjust to your local libclang
cindex.Config.set_library_file("/usr/lib/llvm-18/lib/libclang.so")


def is_extern_c(cursor: cindex.Cursor) -> bool:
    """
    Detect whether a function has C language linkage.
    """
    # In libclang, mangled_name often equals spelling for extern "C"
    if cursor.kind != cindex.CursorKind.FUNCTION_DECL:
        return False

    try:
        mangled = cursor.mangled_name
        if mangled == cursor.spelling:
            return True
    except Exception:
        pass

    return False


def print_functions(node, depth=0):
    for child in node.get_children():
        if child.kind == cindex.CursorKind.FUNCTION_DECL:
            print(f"Function: {child.spelling}")
            print(f"  Kind: {child.kind}")
            print(f"  Location: {child.location.file}:{child.location.line}")

            try:
                print(f"  Linkage: {child.linkage.name}")
            except Exception:
                print("  Linkage: <unknown>")

            try:
                print(f"  Mangled: {child.mangled_name}")
            except Exception:
                print("  Mangled: <none>")

            print(f"  extern \"C\": {is_extern_c(child)}")
            print()

        print_functions(child, depth + 1)


def main(filename):
    index = cindex.Index.create()

    tu = index.parse(
        filename,
        args=[
            "-std=c++17",
        ]
    )

    for diag in tu.diagnostics:
        print(diag)

    print_functions(tu.cursor)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python dump_functions.py file.cpp")
        sys.exit(1)

    main(sys.argv[1])