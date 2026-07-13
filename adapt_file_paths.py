from pathlib import Path
from typing import Final

C_ASSERT_CPP_PATH: Final[Path] = Path(__file__).parent / '_adapt_assert.cpp'
C_ASSERT_HEADER_PATH: Final[Path] = Path(__file__).parent / '_adapt_assert.h'
C_HASH_HEADER_PATH: Final[Path] = Path(__file__).parent / '_adapt_hash.h'
