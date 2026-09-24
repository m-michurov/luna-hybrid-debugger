import json
import re
import sys
from typing import Any, Dict, Iterable, List, Optional, Tuple

from trace.dtypes import AwaitedDF, CF, CodeMetaInformation, DFInfo, Id


DEFAULT_BUILD_DIR: str = 'build'


def _parse_id(value: Any) -> Optional[Id]:
    if value is None:
        return None

    if isinstance(value, (list, tuple)):
        try:
            return tuple(int(it) for it in value)
        except (TypeError, ValueError):
            return None

    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None

        # JSON-like list
        if stripped.startswith('[') and stripped.endswith(']'):
            try:
                raw = json.loads(stripped)
                if isinstance(raw, list):
                    return tuple(int(it) for it in raw)
            except (ValueError, TypeError):
                pass

        # Whitespace/comma separated vector
        parts = [it for it in re.split(r'[\s,;]+', stripped) if it]
        try:
            return tuple(int(it) for it in parts)
        except ValueError:
            return None

    return None


def _parse_ids(values: Any) -> List[Id]:
    if values is None:
        return []

    if not isinstance(values, list):
        return []

    result: List[Id] = []
    for it in values:
        parsed = _parse_id(it)
        if parsed is not None:
            result.append(parsed)

    return result


def _iter_json_candidates(line: str) -> Iterable[dict]:
    stripped = line.strip()
    if not stripped:
        return []

    try:
        obj = json.loads(stripped)
    except json.JSONDecodeError:
        return []

    if isinstance(obj, dict):
        candidates = [obj]
        for key in (
            'payload',
            'data',
            'record',
            'value',
            'fields',
            'event',
            'body',
            'args',
        ):
            nested = obj.get(key)
            if isinstance(nested, dict):
                candidates.append(nested)
        return candidates

    if isinstance(obj, list):
        return [it for it in obj if isinstance(it, dict)]

    return []


def _extract_cf_record(candidate: dict) -> Optional[Tuple[Id, CF]]:
    cf_id = _parse_id(
        candidate.get('id')
        or candidate.get('cf_id')
        or candidate.get('cf')
    )

    history = (
        candidate.get('h')
        or candidate.get('history')
        or candidate.get('call_stack')
        or candidate.get('stack')
    )
    if not isinstance(history, list):
        return None

    if cf_id is None:
        return None

    try:
        history_int = [int(it) for it in history]
    except (TypeError, ValueError):
        return None

    input_ids = set(_parse_ids(
        candidate.get('i')
        or candidate.get('in')
        or candidate.get('input')
        or candidate.get('inputs')
    ))
    output_ids = set(_parse_ids(
        candidate.get('o')
        or candidate.get('out')
        or candidate.get('output')
        or candidate.get('outputs')
    ))

    if not history_int:
        return None

    return (
        cf_id,
        CF(
            input=input_ids,
            output=output_ids,
            history=history_int,
            block=history_int[-1],
        ),
    )


def _to_awaited_df(entry: dict) -> Optional[AwaitedDF]:
    idx = _parse_id(entry.get('i') or entry.get('idx') or entry.get('id'))
    if idx is None:
        return None

    expression = (
        entry.get('n')
        or entry.get('name')
        or entry.get('expression')
        or entry.get('expr')
        or ''
    )
    if not isinstance(expression, str):
        expression = str(expression)

    name_end_index = expression.find('[')
    indices_count = int(entry.get('indices_count', expression.count('[')))
    local_name = expression[:name_end_index] if name_end_index > 0 else expression

    return AwaitedDF(
        idx=idx,
        local_name=local_name,
        indices_count=indices_count,
        expression=expression,
    )


def _extract_pending_record(candidate: dict) -> Optional[Tuple[Id, List[AwaitedDF]]]:
    cf_id = _parse_id(
        candidate.get('id')
        or candidate.get('cf_id')
        or candidate.get('cf')
    )
    if cf_id is None:
        return None

    raw_awaited = (
        candidate.get('w')
        or candidate.get('wait')
        or candidate.get('awaited')
        or candidate.get('pending')
        or []
    )
    if not isinstance(raw_awaited, list):
        return None

    awaited: List[AwaitedDF] = []
    for raw in raw_awaited:
        if not isinstance(raw, dict):
            continue
        awaited_df = _to_awaited_df(raw)
        if awaited_df is not None:
            awaited.append(awaited_df)

    return cf_id, awaited


def _extract_id_record(candidate: dict) -> Optional[Tuple[Id, DFInfo]]:
    idx = _parse_id(
        candidate.get('id')
        or candidate.get('idx')
        or candidate.get('df_id')
    )
    if idx is None:
        return None

    name = (
        candidate.get('name')
        or candidate.get('df_name')
        or candidate.get('df')
    )
    block = (
        candidate.get('block')
        or candidate.get('block_id')
        or candidate.get('decl_block')
    )

    if not isinstance(name, str):
        return None

    try:
        block_int = int(block)
    except (TypeError, ValueError):
        return None

    return idx, DFInfo(name, block_int)


def load_cf_info(files: List[str]) -> Dict[Id, CF]:
    result: Dict[Id, CF] = {}

    for file_name in files:
        with open(file_name, 'rt', encoding='utf-8', errors='ignore') as file:
            for line in file:
                for candidate in _iter_json_candidates(line):
                    loaded = _extract_cf_record(candidate)
                    if loaded is None:
                        continue
                    cf_id, cf = loaded
                    result[cf_id] = cf
                    break

    return result


def load_pending(files: List[str]) -> Dict[Id, List[AwaitedDF]]:
    result: Dict[Id, List[AwaitedDF]] = {}

    for file_name in files:
        with open(file_name, 'rt', encoding='utf-8', errors='ignore') as file:
            for line in file:
                for candidate in _iter_json_candidates(line):
                    loaded = _extract_pending_record(candidate)
                    if loaded is None:
                        continue
                    cf_id, awaited = loaded
                    result[cf_id] = awaited
                    break

    return result


def _parse_luna5_id_map_line(line: str) -> Optional[Tuple[Id, DFInfo]]:
    stripped = line.strip()
    if not stripped or '>' not in stripped:
        return None

    left, right = stripped.split('>', 1)
    try:
        idx = tuple(map(int, left.split()))
        name, block = right.split()
        return idx, DFInfo(name, int(block))
    except (ValueError, TypeError):
        return None


def load_id_map(files: List[str]) -> Dict[Id, DFInfo]:
    result: Dict[Id, DFInfo] = {}

    for file_name in files:
        with open(file_name, 'rt', encoding='utf-8', errors='ignore') as file:
            for line in file:
                # Luna5 native format: "<id tuple>><name> <block>"
                parsed = _parse_luna5_id_map_line(line)
                if parsed is not None:
                    idx, info = parsed
                    result[idx] = info
                    continue

                # EVLOG/JSON fallback
                for candidate in _iter_json_candidates(line):
                    loaded = _extract_id_record(candidate)
                    if loaded is None:
                        continue
                    idx, info = loaded
                    result[idx] = info
                    break

    return result


def load_meta_info(build_dir: str = DEFAULT_BUILD_DIR) -> CodeMetaInformation:
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
