import sys
from pathlib import Path

# Make the repo root importable when pytest is invoked from anywhere.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from calculator import add, subtract


def test_add():
    assert add(2, 3) == 5
    assert add(-2, -3) == -5
    assert add(-2, 3) == 1
    assert add(0, 0) == 0
    assert add(0, 5) == 5


def test_subtract():
    assert subtract(5, 3) == 2
    assert subtract(-5, -3) == -2
    assert subtract(-5, 3) == -8
    assert subtract(0, 0) == 0
    assert subtract(0, 5) == -5
