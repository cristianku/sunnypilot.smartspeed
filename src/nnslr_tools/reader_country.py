"""Country context for the sign reader. GPS supplies a hint, never a class mask."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

UNKNOWN = "unknown"
COUNTRY_DROPOUT = 0.15
PROJECTION = "country_projection.weight"


def country_code(value) -> str:
    return value.strip().upper() if isinstance(value, str) and re.fullmatch(r"[A-Za-z]{2}", value.strip()) else UNKNOWN


def countries_of(checkpoint: dict) -> tuple[str, ...]:
    values = checkpoint.get("country_codes", ())
    if not isinstance(values, (list, tuple)) or (values and (
        values[0] != UNKNOWN or any(country_code(code) == UNKNOWN or country_code(code) != code for code in values[1:]) or
        len(set(values)) != len(values)
    )):
        raise ValueError("invalid classifier country codes")
    return tuple(values)


def contract_codes(contract: dict) -> tuple[str, ...]:
    if "country_input" not in contract:
        return ()
    field = contract["country_input"]
    if not isinstance(field, dict):
        raise ValueError("invalid classifier country input")
    codes = countries_of({"country_codes": field.get("codes")})
    if not codes or (field.get("name"), field.get("encoding"), field.get("dtype"), field.get("shape")) != (
        "country", "one_hot", "float32", [1, len(codes)]
    ):
        raise ValueError("invalid classifier country input")
    return codes


def network_for_country(network, codes, value):
    if not codes:
        return network
    import numpy as np

    country = np.zeros((1, len(codes)), dtype=np.float32)
    country[0, country_index(codes, value)] = 1
    return lambda images: network(images=images, country=country)["logits"]


def country_index(codes: tuple[str, ...], value) -> int:
    try:
        return codes.index(country_code(value))
    except ValueError:
        return 0


def frame_country(root: Path, cache: dict, frame: dict, route: str | None = None) -> str:
    route = frame.get("route_id") or route
    if not route:
        return UNKNOWN
    from nnslr_tools.gps import load_positions

    segment, index = frame.get("segment_index"), frame.get("output_index")
    key = frame.get("frame_key")
    if not key and isinstance(segment, int) and isinstance(index, int):
        key = f"{segment}/{index}"
    if not key:
        return UNKNOWN
    if route not in cache:
        from nnslr_tools.manifest import NnslerManifestError

        try:
            cache[route] = load_positions(root, route)
        except NnslerManifestError:
            cache[route] = {}
    return country_code(cache[route].get(key, {}).get("country"))


def video_countries(root: Path, route: str) -> dict[int, tuple[str, ...]]:
    """Per-segment country timeline from the prepared samples (1 Hz by default)."""
    from bisect import bisect_right
    from collections import defaultdict

    from nnslr_tools.gps import load_positions
    from nnslr_tools.manifest import NnslerManifestError

    points = defaultdict(list)
    try:
        positions = load_positions(root, route)
    except NnslerManifestError:
        return {}
    for row in positions.values():
        segment, second = row.get("segment_index"), row.get("output_index")
        if isinstance(segment, int) and isinstance(second, int) and 0 <= second < 60:
            points[segment].append((second, country_code(row.get("country"))))
    timelines = {}
    for segment, samples in points.items():
        seconds, codes = zip(*sorted(samples))
        timelines[segment] = tuple(codes[i] if (i := bisect_right(seconds, second) - 1) >= 0
                                   and second - seconds[i] < 2 else UNKNOWN for second in range(60))
    return timelines


def assignments_digest(records: list[dict], codes: tuple[str, ...]) -> str:
    indices = (str(country_index(codes, row.get("country"))) for row in records)
    return hashlib.sha256(",".join(indices).encode()).hexdigest()


def build_reader(torch, mobilenet, weights, classes: int, codes: tuple[str, ...]):
    """Preserve the image-only state keys. Fuse country into hidden features before the activation."""
    base = mobilenet(weights=weights)
    base.classifier[-1] = torch.nn.Linear(base.classifier[-1].in_features, classes)
    if not codes:
        return base

    class CountryReader(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.features, self.avgpool, self.classifier = base.features, base.avgpool, base.classifier
            self.country_codes = codes
            self.country_projection = torch.nn.Linear(len(codes), self.classifier[0].out_features, bias=False)
            torch.nn.init.zeros_(self.country_projection.weight)

        def forward(self, images, country):
            hidden = self.classifier[0](self.avgpool(self.features(images)).flatten(1))
            hidden = hidden + self.country_projection(country)
            return self.classifier[3](self.classifier[2](self.classifier[1](hidden)))

    return CountryReader()


def batch_forward(torch, model, images, indices):
    codes = getattr(model, "country_codes", ())
    if not codes:
        return model(images)
    indices = indices.to(images.device, non_blocking=True)
    return model(images, torch.nn.functional.one_hot(indices, len(codes)).to(images.dtype))


def frame_forward(torch, model, images, value):
    codes = getattr(model, "country_codes", ())
    if not codes:
        return model(images)
    country = images.new_zeros((len(images), len(codes)))
    country[:, country_index(codes, value)] = 1
    return model(images, country)
