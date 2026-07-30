import json
import os

DEFAULT_PATH = os.environ.get(
    "SIGNAL_CATALOG", "/opt/alice-ingest/init/signal_catalog.json")

_cache = {}


class UnknownSignal(Exception):
    pass


def load(path=None):
    path = path or DEFAULT_PATH
    cached = _cache.get(path)
    if cached is None:
        with open(path) as fh:
            cached = json.load(fh)
        _cache[path] = cached
    return cached


def sentinel(name, path=None):
    return (load(path).get("sentinels") or {}).get(name, "none")


def detector(name, path=None):
    meta = (load(path).get("detectors") or {}).get(name)
    if meta is None:
        raise UnknownSignal(
            f"detector {name!r} is not in the signal catalog; entity kind must "
            f"be declared explicitly, never inferred from an index name")
    return meta


def monitor(name, path=None):
    meta = (load(path).get("monitors") or {}).get(name)
    if meta is None:
        raise UnknownSignal(
            f"monitor {name!r} is not in the signal catalog")
    return meta


def detector_names(path=None):
    return set((load(path).get("detectors") or {}).keys())


def monitor_names(path=None):
    return set((load(path).get("monitors") or {}).keys())


def entity_pairs(source):
    ents = source.get("entity") or []
    if isinstance(ents, dict):
        ents = [ents]
    out = []
    for e in ents:
        if not isinstance(e, dict):
            continue
        n, v = e.get("name"), e.get("value")
        if n is not None and v is not None:
            out.append((str(n), str(v)))
    return out


def entity_of(name, source, path=None):
    meta = detector(name, path)
    field = meta.get("category_field")
    pairs = entity_pairs(source)
    if not field:
        return (meta["entity_kind"],
                meta.get("entity_id") or sentinel("entity_id", path),
                "")
    for n, v in pairs:
        if n == field:
            return meta["entity_kind"], v, n
    if pairs:
        raise UnknownSignal(
            f"detector {name!r} declares category_field {field!r} but the "
            f"result carries {[n for n, _ in pairs]}")
    return (meta["entity_kind"], sentinel("entity_id", path), field)


def scope_string(source):
    pairs = entity_pairs(source)
    if not pairs:
        return "", "whole fleet"
    return ",".join(n for n, _ in pairs), ",".join(v for _, v in pairs)
