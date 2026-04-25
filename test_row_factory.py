import sqlite3
from peewee import SqliteDatabase, Model, CharField

db = SqliteDatabase(':memory:')

class TestModel(Model):
    name = CharField()
    class Meta:
        database = db

db.connect()
db.create_tables([TestModel])
TestModel.create(name='test')

conn = db.connection()
print(f"Row factory: {conn.row_factory}")

try:
    row = conn.execute("SELECT * FROM testmodel").fetchone()
    print(f"Row type: {type(row)}")
    print(f"Accessing by name 'name': {row['name']}")
except Exception as e:
    print(f"Failed accessing by name: {e}")

conn.row_factory = sqlite3.Row
row = conn.execute("SELECT * FROM testmodel").fetchone()
print(f"Accessing by name after setting row_factory: {row['name']}")
