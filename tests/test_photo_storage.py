import os
import uuid
import pytest
from pathlib import Path
from press_to_talk.storage.providers.sqlite_fts import SQLiteFTS5RememberStore
from press_to_talk.storage.models import RememberItemRecord, db

@pytest.fixture
def store(tmp_path):
    db_path = tmp_path / "test_remember.sqlite3"
    # Ensure Peewee db is clean for each test if possible, or use a unique path
    store = SQLiteFTS5RememberStore(user_id="test_user", db_path=db_path)
    yield store
    if db.database:
        db.close()

def test_add_retrieval_with_photo(store):
    photo_path = "/path/to/photo.jpg"
    memory_text = "Met a cat"
    
    # This should FAIL initially because add() doesn't accept photo_path
    try:
        store.add(memory=memory_text, photo_path=photo_path)
    except TypeError as e:
        pytest.fail(f"add() failed with TypeError: {e}")
    
    records = store.list_all()
    assert len(records) > 0
    assert records[0].memory == memory_text
    assert records[0].photo_path == photo_path

def test_update_with_photo(store):
    memory_text = "Met a cat"
    store.add(memory=memory_text)
    
    records = store.list_all()
    memory_id = records[0].id
    
    new_photo_path = "/new/path/to/photo.jpg"
    # This should FAIL initially because update() doesn't accept photo_path
    store.update(memory_id=memory_id, memory=memory_text, photo_path=new_photo_path)
    
    updated_records = store.list_all()
    assert updated_records[0].photo_path == new_photo_path
