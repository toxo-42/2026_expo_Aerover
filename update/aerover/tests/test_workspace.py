from pathlib import Path

import pytest

from src.core.workspace import SOURCE_FILE, ModelStore


@pytest.fixture
def repo(tmp_path: Path) -> tuple[ModelStore, Path]:
    root = tmp_path / "aerover"
    (root / "sessions" / "20260907_1").mkdir(parents=True)
    return ModelStore(models_dir=root / "3D_model", root=root), root


def test_creates_folder_and_records_relative_source(repo):
    store, root = repo
    session = root / "sessions" / "20260907_1"
    assert store.find(session) is None
    out = store.dir_for(session)                          # create=False 는 이름만 정한다
    assert out == root / "3D_model" / "20260907_1" and not out.exists()

    out = store.dir_for(session, create=True)
    assert out.is_dir()
    assert (out / SOURCE_FILE).read_text(encoding="utf-8").strip() == "sessions/20260907_1"
    assert store.find(session) == out
    assert store.dir_for(session) == out                  # 두 번 불러도 같은 곳


def test_same_name_from_a_different_source_gets_a_suffix(repo, tmp_path):
    store, root = repo
    a = root / "sessions" / "20260907_1"
    b = tmp_path / "elsewhere" / "20260907_1"
    b.mkdir(parents=True)
    out_a = store.dir_for(a, create=True)
    out_b = store.dir_for(b, create=True)
    assert out_b == root / "3D_model" / "20260907_1_2"
    assert store.find(a) == out_a and store.find(b) == out_b


def test_source_outside_root_is_still_relative(repo, tmp_path):
    store, root = repo
    outside = tmp_path / "ODM_testing" / "images_sub"
    outside.mkdir(parents=True)
    assert store.encode(outside) == "../ODM_testing/images_sub"
    out = store.dir_for(outside, create=True)
    assert store.find(outside) == out


def test_record_written_on_another_computer_matches_by_folder_name(repo):
    store, root = repo
    session = root / "sessions" / "20260907_1"
    legacy = root / "3D_model" / "20260907_1"
    legacy.mkdir(parents=True)
    (legacy / SOURCE_FILE).write_text("/Users/someone/dev/aerover/sessions/20260907_1\n")
    assert store.find(session) == legacy

    (legacy / SOURCE_FILE).write_text("/Users/someone/dev/aerover/sessions/other\n")
    assert store.find(session) is None


def test_existing_local_path_must_match_exactly(repo):
    store, root = repo
    session = root / "sessions" / "20260907_1"
    (root / "other" / "20260907_1").mkdir(parents=True)
    d = root / "3D_model" / "20260907_1"
    d.mkdir(parents=True)
    (d / SOURCE_FILE).write_text("other/20260907_1\n")      # 이름은 같지만 실제로 다른 폴더
    assert store.find(session) is None
