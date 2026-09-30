"""A name -> item registry, the base of the tool, agent and artifact type registries."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any, ClassVar


class UnknownNameError(KeyError):
    """Nothing is registered under this name."""

    kind: ClassVar[str] = "name"

    def __str__(self) -> str:  # KeyError would print only the repr of the name
        return f"unknown {self.kind} {self.args[0]!r}"


class Registry[T]:
    """One item per name, kept in registration order.

    Subclasses set ``item_type``, the ``key`` attribute that names an item, and
    the ``unknown_error`` raised by ``get()`` (its ``kind`` names the items in
    every message). Registering the same object twice is harmless; a second
    object under a taken name is refused.
    """

    item_type: ClassVar[type]
    key: ClassVar[str] = "name"
    unknown_error: ClassVar[type[UnknownNameError]] = UnknownNameError

    def __init__(self, items: Iterable[T] = ()) -> None:
        self._items: dict[str, T] = {}
        for item in items:
            self.register(item)

    def register(self, item: T) -> T:
        if not isinstance(item, self.item_type):
            raise TypeError(f"{self.item_type.__name__} expected, not {item!r}")
        name = getattr(item, self.key)
        existing = self._items.get(name)
        if existing is not None and existing is not item:
            raise ValueError(f"{self.unknown_error.kind} {name!r} is already registered")
        self._items[name] = item
        return item

    def get(self, name: str) -> T:
        try:
            return self._items[name]
        except KeyError:
            raise self.unknown_error(name) from None

    def names(self) -> frozenset[str]:
        return frozenset(self._items)

    def __contains__(self, name: Any) -> bool:
        return name in self._items

    def __iter__(self) -> Iterator[T]:
        return iter(self._items.values())

    def __len__(self) -> int:
        return len(self._items)
