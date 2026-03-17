from __future__ import annotations


DEFAULT_STREAMS: list[tuple[int, str]] = [
    (5000, "Computer 1"),
    (5002, "Computer 2"),
]


def default_stream_args() -> list[str]:
    return [f"{port}={label}" for port, label in DEFAULT_STREAMS]


def parse_streams(stream_args: list[str] | None) -> list[tuple[int, str]]:
    if not stream_args:
        return DEFAULT_STREAMS.copy()

    streams: list[tuple[int, str]] = []

    for raw_item in stream_args:
        if "=" not in raw_item:
            raise ValueError(f"Invalid stream '{raw_item}'. Expected format <port>=<label>.")

        raw_port, raw_label = raw_item.split("=", 1)
        raw_port = raw_port.strip()
        label = raw_label.strip()

        if not raw_port.isdigit():
            raise ValueError(f"Invalid port in stream '{raw_item}'. Port must be numeric.")

        port = int(raw_port)
        if not label:
            raise ValueError(f"Invalid label in stream '{raw_item}'. Label cannot be empty.")

        streams.append((port, label))

    return streams
