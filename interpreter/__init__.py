from ._version import VERSION as __version__

__all__ = ["BaseLanguage", "OpenInterpreter", "__version__", "computer", "interpreter"]


def __getattr__(name):
    if name == "BaseLanguage":
        from .core.computer.terminal.base_language import BaseLanguage

        return BaseLanguage
    if name == "OpenInterpreter":
        from .core.core import OpenInterpreter

        return OpenInterpreter
    if name in {"computer", "interpreter"}:
        from .core.core import OpenInterpreter

        instance = OpenInterpreter()
        globals()["interpreter"] = instance
        globals()["computer"] = instance.computer
        return globals()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
