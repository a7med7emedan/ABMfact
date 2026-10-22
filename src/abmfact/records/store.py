"""A content-addressed run store: identical requests are served from disk instead of rerun.

Layout::

    root/runs/<key[:2]>/<key>/record.json      the run record
                              meta.json        type names and saved times
                              scalars.json     v0.1-style outputs
                              cells.npz | cells.parquet
                              fields.npz | fields.zarr
                              observables.csv | observables.parquet
    root/index.jsonl | root/index.duckdb       one row per run, for queries

The key is the SHA-256 of the canonical run request (:meth:`RunRequest.key`). With the ``[store]`` extra
installed, cells and observables are written as Parquet, fields as Zarr and the index to DuckDB;
otherwise NumPy ``.npz``, CSV and JSON lines. Only one process should write to a store at a time;
parallel runs return their records to the parent, which stores them.
"""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import os
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np

from .record import RunRecord, RunRequest
from .schema import CELL_COLUMNS, FieldFrame, RunOutputs, cells_table


def _have(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def _sha256(path: Path) -> str:
    if path.is_dir():
        h = hashlib.sha256()
        for f in sorted(p for p in path.rglob("*") if p.is_file()):
            h.update(f.relative_to(path).as_posix().encode())
            h.update(f.read_bytes())
        return h.hexdigest()
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, obj: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=True, allow_nan=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


class RunStore:
    """Store and look up runs by request.

    ``formats`` is ``"auto"`` (Parquet and Zarr when installed), ``"columnar"`` (require them) or
    ``"basic"`` (NumPy and CSV only). ``index`` is ``"auto"``, ``"duckdb"`` or ``"jsonl"``.
    """

    def __init__(self, root: str | Path, formats: str = "auto", index: str = "auto"):
        self.root = Path(root)
        (self.root / "runs").mkdir(parents=True, exist_ok=True)
        columnar = _have("pyarrow") and _have("zarr")
        if formats == "columnar" and not columnar:
            raise RuntimeError("columnar formats need: pip install 'abmfact[store]'")
        self.columnar = columnar if formats == "auto" else formats == "columnar"
        if index == "duckdb" and not _have("duckdb"):
            raise RuntimeError("a DuckDB index needs: pip install 'abmfact[store]'")
        self.index_kind = ("duckdb" if _have("duckdb") else "jsonl") if index == "auto" else index

    # -- locations ---------------------------------------------------------------------------------------

    def run_dir(self, key: str) -> Path:
        return self.root / "runs" / key[:2] / key

    @property
    def index_path(self) -> Path:
        return self.root / ("index.duckdb" if self.index_kind == "duckdb" else "index.jsonl")

    # -- reading -----------------------------------------------------------------------------------------

    def get(self, request: RunRequest) -> RunRecord | None:
        """The stored record for this request with ``status="cached"``, or ``None``. Failed runs miss."""
        path = self.run_dir(request.key()) / "record.json"
        if not path.exists():
            return None
        record = RunRecord.from_dict(json.loads(path.read_text(encoding="utf-8")))
        if record.status != "ok":
            return None
        return replace(record, status="cached", outputs=self.load_outputs(record))

    def load_outputs(self, record: RunRecord) -> RunOutputs:
        d = self.run_dir(record.run_id)
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        out = RunOutputs(type_names=list(meta["type_names"]), save_times=list(meta["save_times"]))
        out.scalars = json.loads((d / "scalars.json").read_text(encoding="utf-8"))
        paths = record.output_paths
        if "cells" in paths:
            out.cells = self._read_cells(d / paths["cells"])
        if "fields" in paths:
            out.fields = self._read_fields(d / paths["fields"])
        if "observables" in paths:
            out.observables = self._read_observables(d / paths["observables"])
        return out

    # -- writing -----------------------------------------------------------------------------------------

    def put(self, record: RunRecord) -> RunRecord:
        """Write a record and its in-memory outputs. Returns the record with paths and hashes filled."""
        d = self.run_dir(record.run_id)
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
        paths: dict[str, str] = {}
        out = record.outputs
        if out is not None:
            _write_json(d / "meta.json", {"type_names": out.type_names, "save_times": out.save_times})
            _write_json(d / "scalars.json", out.scalars)
            if out.cells["x"].size:
                paths["cells"] = self._write_cells(d, out)
            if out.fields:
                paths["fields"] = self._write_fields(d, out.fields)
            if out.observables["time"].size:
                paths["observables"] = self._write_observables(d, out)
        if record.log_path and Path(record.log_path).is_file():
            shutil.copyfile(record.log_path, d / "log.txt")
            paths["log"] = "log.txt"
        record = replace(
            record,
            output_paths=paths,
            output_sha256={k: _sha256(d / v) for k, v in paths.items()},
            log_path=str(d / "log.txt") if "log" in paths else record.log_path,
        )
        _write_json(d / "record.json", record.to_dict())
        self._index_add(record)
        return record

    # -- formats -----------------------------------------------------------------------------------------

    def _write_cells(self, d: Path, out: RunOutputs) -> str:
        if self.columnar:
            import pyarrow as pa
            import pyarrow.parquet as pq

            table = pa.table({c: out.cells[c] for c in CELL_COLUMNS})
            pq.write_table(table, d / "cells.parquet")
            return "cells.parquet"
        arrays: dict[str, Any] = {c: out.cells[c] for c in CELL_COLUMNS}
        np.savez_compressed(d / "cells.npz", **arrays)
        return "cells.npz"

    def _read_cells(self, path: Path) -> dict[str, np.ndarray]:
        if path.suffix == ".parquet":
            import pyarrow.parquet as pq

            table = pq.read_table(path)
            return cells_table({c: table.column(c).to_numpy() for c in CELL_COLUMNS})
        with np.load(path) as z:
            return cells_table({c: z[c] for c in CELL_COLUMNS})

    def _write_fields(self, d: Path, frames: list[FieldFrame]) -> str:
        meta = [{"time": f.time, "substrate": f.substrate, "origin": list(f.origin), "spacing": list(f.spacing)}
                for f in frames]  # fmt: skip
        if self.columnar:
            import zarr

            group: Any = zarr.open_group(str(d / "fields.zarr"), mode="w")
            group.attrs["frames"] = meta
            for i, f in enumerate(frames):
                arr = group.create_array(name=f"f{i}", shape=f.values.shape, dtype=f.values.dtype)
                arr[...] = f.values
            return "fields.zarr"
        arrays: dict[str, Any] = {f"f{i}": f.values for i, f in enumerate(frames)}
        np.savez_compressed(d / "fields.npz", meta=np.asarray(json.dumps(meta)), **arrays)
        return "fields.npz"

    def _read_fields(self, path: Path) -> list[FieldFrame]:
        if path.suffix == ".zarr":
            import zarr

            group: Any = zarr.open_group(str(path), mode="r")
            meta: Any = group.attrs["frames"]
            arrays = [np.asarray(group[f"f{i}"][...]) for i in range(len(meta))]
        else:
            with np.load(path) as z:
                meta = json.loads(str(z["meta"]))
                arrays = [z[f"f{i}"] for i in range(len(meta))]
        return [
            FieldFrame(m["time"], m["substrate"], tuple(m["origin"]), tuple(m["spacing"]), a)
            for m, a in zip(meta, arrays, strict=True)
        ]

    def _write_observables(self, d: Path, out: RunOutputs) -> str:
        obs = out.observables
        if self.columnar:
            import pyarrow as pa
            import pyarrow.parquet as pq

            names = [str(n) for n in obs["name"]]
            pq.write_table(pa.table({"time": obs["time"], "name": names, "value": obs["value"]}),
                           d / "observables.parquet")  # fmt: skip
            return "observables.parquet"
        with (d / "observables.csv").open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["time", "name", "value"])
            for t, n, v in zip(obs["time"], obs["name"], obs["value"], strict=True):
                w.writerow([repr(float(t)), n, repr(float(v))])
        return "observables.csv"

    def _read_observables(self, path: Path) -> dict[str, np.ndarray]:
        if path.suffix == ".parquet":
            import pyarrow.parquet as pq

            t = pq.read_table(path)
            return {
                "time": t.column("time").to_numpy(),
                "name": np.asarray(t.column("name").to_pylist(), dtype=object),
                "value": t.column("value").to_numpy(),
            }
        with path.open(newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        return {
            "time": np.asarray([float(r["time"]) for r in rows]),
            "name": np.asarray([r["name"] for r in rows], dtype=object),
            "value": np.asarray([float(r["value"]) for r in rows]),
        }

    # -- index -------------------------------------------------------------------------------------------

    @staticmethod
    def _row(record: RunRecord) -> dict[str, Any]:
        req = record.request
        return {
            "run_id": record.run_id,
            "engine": req.engine,
            "engine_version": req.engine_version,
            "image_digest": req.image_digest,
            "model_hash": req.model_hash,
            "seed": req.seed,
            "params": json.dumps(req.params, sort_keys=True),
            "knobs": json.dumps(req.knobs, sort_keys=True),
            "status": record.status,
            "wall_time": record.wall_time,
            "created": record.created,
        }

    def _duck(self) -> Any:
        import duckdb

        con = duckdb.connect(str(self.index_path))
        con.execute(
            "CREATE TABLE IF NOT EXISTS runs (run_id VARCHAR PRIMARY KEY, engine VARCHAR, engine_version VARCHAR, "
            "image_digest VARCHAR, model_hash VARCHAR, seed BIGINT, params VARCHAR, knobs VARCHAR, status VARCHAR, "
            "wall_time DOUBLE, created VARCHAR)"
        )
        return con

    def _index_add(self, record: RunRecord) -> None:
        row = self._row(record)
        if self.index_kind == "duckdb":
            con = self._duck()
            try:
                cols = ", ".join(row)
                marks = ", ".join("?" for _ in row)
                con.execute(f"INSERT OR REPLACE INTO runs ({cols}) VALUES ({marks})", list(row.values()))  # noqa: S608
            finally:
                con.close()
            return
        with self.index_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")

    def query(self, **filters: Any) -> list[dict[str, Any]]:
        """Index rows matching every ``column=value`` filter, latest write per run, ordered by run id."""
        if self.index_kind == "duckdb":
            if not self.index_path.exists():
                return []
            con = self._duck()
            try:
                where = " AND ".join(f"{k} = ?" for k in filters) or "TRUE"
                cur = con.execute(f"SELECT * FROM runs WHERE {where} ORDER BY run_id", list(filters.values()))  # noqa: S608
                names = [c[0] for c in cur.description]
                return [dict(zip(names, r, strict=True)) for r in cur.fetchall()]
            finally:
                con.close()
        latest: dict[str, dict[str, Any]] = {}
        if self.index_path.exists():
            for line in self.index_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    row = json.loads(line)
                    latest[row["run_id"]] = row
        rows = [r for _, r in sorted(latest.items()) if all(r.get(k) == v for k, v in filters.items())]
        return rows

    def reindex(self) -> int:
        """Rebuild the index from the stored records. Returns the number of runs indexed."""
        if self.index_path.exists():
            self.index_path.unlink()
        n = 0
        for path in sorted((self.root / "runs").glob("*/*/record.json")):
            self._index_add(RunRecord.from_dict(json.loads(path.read_text(encoding="utf-8"))))
            n += 1
        return n
