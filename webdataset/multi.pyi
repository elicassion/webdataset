from typing import Any

the_protocol: Any
all_pids: Any

class Finished:
    def __init__(self, **kw: Any) -> None: ...

def reader(dataset: Any, sockname: Any, index: Any) -> None: ...

class MultiLoader:
    dataset: Any = ...
    workers: Any = ...
    verbose: Any = ...
    pids: Any = ...
    socket: Any = ...
    ctx: Any = ...
    nokill: Any = ...
    prefix: Any = ...
    def __init__(self, dataset: Any, workers: int = ..., verbose: bool = ..., nokill: bool = ..., prefix: str = ...) -> None: ...
    def kill(self) -> None: ...
    sockname: Any = ...
    def __iter__(self) -> Any: ...

class DistSender:
    sockname: Any = ...
    ctx: Any = ...
    sock: Any = ...
    def __init__(self, sockname: Any) -> None: ...
    def send(self, sample: Any) -> None: ...

class DistLoader:
    sockname: Any = ...
    def __init__(self, sockname: Any) -> None: ...
    ctx: Any = ...
    def __iter__(self) -> Any: ...