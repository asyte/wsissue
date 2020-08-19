from db import db

class InfoModel(db.Model):
    __tablename__ = 'info'
    some_data = db.Column(db.String(16), nullable=False, default='')
