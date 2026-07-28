"""Broker public API with lazy exports to keep provider imports acyclic."""

__all__ = ["SearchBroker", "create_broker"]


def __getattr__(name: str):
    if name in __all__:
        from argus.broker.router import SearchBroker, create_broker

        return {"SearchBroker": SearchBroker, "create_broker": create_broker}[name]
    raise AttributeError(name)
