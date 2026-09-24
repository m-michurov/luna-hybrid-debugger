from collections import namedtuple
from typing import Dict, List, Tuple, Union

Id = Tuple[int, ...]

CF = namedtuple(
    'CF',
    [
        'input',
        'output',
        'history',
        'block'
    ]
)

CallHistory = Union[List[int], Tuple[int, ...]]

AwaitedDF = namedtuple(
    'AwaitedDF',
    [
        'idx',
        'local_name',
        'indices_count',
        'expression'
    ]
)

DFInfo = namedtuple(
    'DF',
    ['name', 'block']
)


class CodeMetaInformation:
    def __init__(
            self,
            blocks_info: dict,
            ti: dict
    ) -> None:
        self.blocks_info = blocks_info
        self.ti = ti
        self.source_lines: Dict[str, List[str]] = {}

    def comment(self, block: int) -> str:
        return self.blocks_info[str(block)]['comment']

    def line_and_position(self, block: int) -> Tuple[str, str]:
        """Returns position as <file>:<line> and line as string"""
        pos = self.blocks_info[str(block)]['pos']

        fid, ln, _, _ = self.ti['text'][pos]

        path = self.ti['paths'][str(fid)]

        if path not in self.source_lines:
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                self.source_lines[path] = f.readlines()

        line = self.source_lines[path][ln].strip().strip('{;').strip().strip('@')
        return line, f'{path}:{ln + 1}'
