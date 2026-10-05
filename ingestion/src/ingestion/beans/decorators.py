"""Trusted decorator metadata; decorators do not instantiate objects."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

T = TypeVar("T", bound=type[Any])


@dataclass(frozen=True)
class BeanDefinition:
    contract: type[Any]
    name: str


def component(*, contract: type[Any], name: str) -> Callable[[T], T]:
    if not name.strip():
        raise ValueError("Component name is required")

    def decorate(implementation: T) -> T:
        implementation.__bean_definition__ = BeanDefinition(contract, name)
        return implementation

    return decorate
