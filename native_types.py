"""Small local data types for the port; no AstrBot runtime or fake AstrBot modules."""
from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar('T')

class FunctionTool(Generic[T]):
    """Tool specifications consumed by Hermes' registry adapter."""

@dataclass
class ToolExecResult:
    is_success: bool = True
    description: str = ''

    def __str__(self):
        return self.description

class AgentContext:
    pass

@dataclass
class ContextWrapper(Generic[T]):
    context: T

class StarTools:
    @staticmethod
    def get_data_dir(name):
        from hermes_constants import get_hermes_home
        path = get_hermes_home() / 'bilibili'
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        return path

@dataclass
class Plain:
    text: str

@dataclass
class Image:
    file: str

    @classmethod
    def fromFileSystem(cls, path):
        return cls(str(path))

    @classmethod
    def fromURL(cls, url):
        return cls(str(url))

class MessageChain:
    def __init__(self):
        self.chain = []

    def message(self, text):
        self.chain.append(Plain(str(text)))
        return self

    def chain_result(self, chain):
        self.chain.extend(chain)
        return self

@dataclass
class Node:
    content: list
    name: str = ''
    uin: str = ''

@dataclass
class Nodes:
    nodes: list

Context = AgentContext
