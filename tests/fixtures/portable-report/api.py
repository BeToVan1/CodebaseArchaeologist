from fastapi import APIRouter
from repository import list_items

router = APIRouter()


@router.get("/items")
def list_items_route():
    return list_items()
