"""Small constructor-injection container with named implementation support."""

from __future__ import annotations

import inspect
from collections.abc import Iterable
from typing import Any, TypeVar, cast, get_type_hints

from ingestion.beans.decorators import BeanDefinition

BeanT = TypeVar("BeanT")


class BeanContainer:
    def __init__(self) -> None:
        self._instances: dict[tuple[type[Any], str | None], Any] = {}
        self._components: dict[tuple[type[Any], str], type[Any]] = {}
        self._constructing: set[tuple[type[Any], str | None]] = set()

    def register_instance(
        self, instance: object, *, contract: type[Any] | None = None, name: str | None = None
    ) -> None:
        key = (contract or type(instance), name)
        if key in self._instances:
            raise ValueError(f"Duplicate bean: {key[0].__name__}:{name or 'default'}")
        self._instances[key] = instance

    def register_components(self, implementations: Iterable[type[Any]]) -> None:
        for implementation in implementations:
            definition = getattr(implementation, "__bean_definition__", None)
            if not isinstance(definition, BeanDefinition):
                raise ValueError(f"{implementation.__name__} is missing @component")
            key = (definition.contract, definition.name)
            if key in self._components:
                raise ValueError(
                    f"Duplicate component: {definition.contract.__name__}:{definition.name}"
                )
            self._components[key] = implementation

    def select(self, contract: type[Any], name: str) -> object:
        key = (contract, name)
        if key not in self._components:
            available = sorted(candidate for (item, candidate) in self._components if item is contract)
            raise ValueError(f"Unknown {contract.__name__} '{name}'. Available: {available}")
        return self._construct(self._components[key], key)

    def bind_selected(self, contract: type[Any], name: str) -> None:
        self.register_instance(self.select(contract, name), contract=contract)

    def create(self, implementation: type[BeanT]) -> BeanT:
        return cast(BeanT, self._construct(implementation, (implementation, None)))

    def resolve(self, contract: type[Any]) -> object:
        key = (contract, None)
        if key in self._instances:
            return self._instances[key]
        raise ValueError(f"No default bean registered for {contract.__name__}")

    def _construct(self, implementation: type[Any], key: tuple[type[Any], str | None]) -> object:
        if key in self._instances:
            return self._instances[key]
        if key in self._constructing:
            raise ValueError(f"Circular bean dependency at {implementation.__name__}")
        self._constructing.add(key)
        try:
            signature = inspect.signature(implementation.__init__)
            hints = get_type_hints(implementation.__init__)
            arguments: dict[str, object] = {}
            for parameter in signature.parameters.values():
                if parameter.name == "self":
                    continue
                if parameter.kind in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD):
                    continue
                dependency = hints.get(parameter.name)
                if dependency is None:
                    raise ValueError(f"Untyped dependency {implementation.__name__}.{parameter.name}")
                arguments[parameter.name] = self.resolve(dependency)
            instance = implementation(**arguments)
            self._instances[key] = instance
            return instance
        finally:
            self._constructing.remove(key)
