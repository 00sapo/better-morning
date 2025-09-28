from typing import List
from pydantic import BaseModel, HttpUrl
import toml

class RSSFeed(BaseModel):
    url: HttpUrl
    name: str

class Collection(BaseModel):
    name: str
    feeds: List[RSSFeed]

def load_config(path: str) -> Collection:
    try:
        data = toml.load(path)
        return Collection(**data)
    except Exception as e:
        print(f"Error loading or validating config: {e}")
        raise