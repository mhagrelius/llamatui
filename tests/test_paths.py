"""paths owns per-user on-disk locations; the DB and whisper assets share one root."""

from llamatui import paths, storage


def test_user_data_dir_is_absolute():
    assert paths.user_data_dir().is_absolute()


def test_default_whisper_dir_under_user_data_dir():
    assert paths.default_whisper_dir().parent == paths.user_data_dir()
    assert paths.default_whisper_dir().name == "whisper"


def test_db_and_whisper_share_root():
    assert storage.default_db_path().parent == paths.user_data_dir()
