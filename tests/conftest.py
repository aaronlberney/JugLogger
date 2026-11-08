import sqlite3
from pathlib import Path

import pytest

from juglogger import demo
from juglogger.boarddb import BoardDB


@pytest.fixture(scope="session")
def board_files(tmp_path_factory):
    root = tmp_path_factory.mktemp("board")
    return demo.build(root)


@pytest.fixture
def board(board_files):
    return BoardDB(board_files[0])


@pytest.fixture
def climbs(board_files):
    with sqlite3.connect(board_files[0]) as c:
        return [row[0] for row in c.execute("SELECT uuid FROM climbs ORDER BY name")]
