# Visualisation

`abmfact visualise` draws results from the common output schema, the same for every engine. Plots and movies need
the `[viz]` extra (matplotlib, imageio and imageio-ffmpeg). The VTK export needs nothing.

```bash
abmfact visualise results/ensemble                 # a mode's output folder, or its JSON file
abmfact run-model examples/ir/spheroid.yaml --out results/spheroid
abmfact visualise results/spheroid --times all --colour state --movie mp4 --vtk --out results/spheroid-plots
abmfact visualise 1e39fdb575 --store runs/ --out plots/    # a run id, or its first characters, in a store
```

| Input | Plots |
|---|---|
| `ensemble.json` | each observable over time: the mean, its interval, and the 5% to 95% band |
| `sweep.json` | each metric's point means, with intervals, against each parameter |
| `indices.json` | Sobol S1 and ST, or Morris mu*, with intervals |
| `calibration.json` | each evaluation's distance with the best so far, and the best fit against the target |
| a run | cell snapshots coloured by type or state, substrate fields (the middle slice in 3D), movies, VTK files |

For a run, `--times` takes `last` (the default), `all`, or comma-separated times (the nearest saves are used).
`--field` limits the substrates drawn. `--movie gif` writes through Pillow. `--movie mp4` writes H.264 through
imageio-ffmpeg, which runs a separate ffmpeg program.

`--vtk` writes `cells.pvd` and one `field_<substrate>.pvd` per substrate, each a time series that ParaView opens
directly. Cells become `.vtp` PolyData, one point per cell, with id, type, state, volume and equivalent radius.
Fields become `.vti` ImageData on their voxel grid.
