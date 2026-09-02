from sqlalchemy import select
from models import ItemModel


def list_items():
    return select(ItemModel)
