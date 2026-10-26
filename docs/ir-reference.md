# ABM-IR 0.1 reference

Generated from the pydantic models by `scripts/make_ir_reference.py`; the JSON Schema is
[`schemas/abm-ir-0.1.json`](https://github.com/a7med7emedan/ABMfact/blob/main/schemas/abm-ir-0.1.json).
Units follow the
model's `units` (micron and minute by default). Unknown fields are rejected, and every name a model
refers to (a substrate, a cell type, a rule's signal or behaviour) must exist in the model.

## IRModel

An ABM-IR document.

| Field | Type | Default |
|---|---|---|
| `schema_version` | `0.1` | `0.1` |
| `name` | string (non-empty) | required |
| `description` | string | empty |
| `units` | [Units](#units) | defaults |
| `space` | [Space](#space) | required |
| `substrates` | list of [Substrate](#substrate) | empty list |
| `cell_types` | list of [CellType](#celltype) | required |
| `rules` | list of [Rule](#rule) | empty list |
| `intracellular` | list of [IntracellularModel](#intracellularmodel) | empty list |
| `initial_conditions` | [InitialConditions](#initialconditions) | defaults |
| `events` | list of [TimedEvent](#timedevent) | empty list |
| `observables` | list of [Observable](#observable) | empty list |
| `experiment` | [Experiment](#experiment) | required |
| `provenance` | [Provenance](#provenance) | defaults |
| `native_extensions` | list of [NativeExtension](#nativeextension) | empty list |

## Units

The units of length and time that the model's quantities are given in.

| Field | Type | Default |
|---|---|---|
| `space` | string | `micron` |
| `time` | string | `min` |

## Space

The simulation domain: its dimension, bounds, substrate grid, and whether it is closed or periodic.

| Field | Type | Default |
|---|---|---|
| `dimensions` | `2` \| `3` | `2` |
| `bounds` | [Bounds](#bounds) | required |
| `voxel` | [Voxel](#voxel) | required |
| `boundary` | `closed` \| `periodic` | `closed` |

## Bounds

The extent of the domain along each axis. Each minimum must be below its maximum.

| Field | Type | Default |
|---|---|---|
| `x_min` | number | required |
| `x_max` | number | required |
| `y_min` | number | required |
| `y_max` | number | required |
| `z_min` | number | `-10.0` |
| `z_max` | number | `10.0` |

## Voxel

The size of one voxel of the substrate grid. In a 2D model ``dz`` still sets the voxel volume.

| Field | Type | Default |
|---|---|---|
| `dx` | number (> 0) | required |
| `dy` | number (> 0) | required |
| `dz` | number (> 0) | `20.0` |

## Substrate

A diffusing substance, with its diffusion coefficient, decay rate, initial value and boundary.

| Field | Type | Default |
|---|---|---|
| `name` | string (non-empty) | required |
| `units` | string | `dimensionless` |
| `diffusion_coefficient` | number (>= 0) | required |
| `decay_rate` | number (>= 0) | `0.0` |
| `initial_value` | number (>= 0) | `0.0` |
| `boundary` | [SubstrateBoundary](#substrateboundary) | defaults |

## SubstrateBoundary

``dirichlet`` holds every side at ``value``; ``sides`` overrides one side, ``None`` makes it no-flux.

| Field | Type | Default |
|---|---|---|
| `kind` | `no_flux` \| `dirichlet` | `no_flux` |
| `value` | number (>= 0) | `0.0` |
| `sides` | map of number (>= 0) or null | empty |

## CellType

One cell type: its volume, cycle, death, mechanics, motility, secretion and interactions. ``transformations`` maps other cell type names to the rate of turning into that type. ``custom`` holds named numeric custom data.

| Field | Type | Default |
|---|---|---|
| `name` | string (non-empty) | required |
| `volume` | number (> 0) | `2494.0` |
| `cycle` | [Cycle](#cycle) | defaults |
| `death` | [Death](#death) | defaults |
| `mechanics` | [Mechanics](#mechanics) | defaults |
| `motility` | [Motility](#motility) | defaults |
| `secretion` | list of [Secretion](#secretion) | empty list |
| `interactions` | [Interactions](#interactions) | defaults |
| `transformations` | map of number (>= 0) | empty |
| `custom` | map of number | empty |

## Cycle

A cell cycle. ``live`` has one phase whose exit rate is the division rate. Give either ``rates`` (phase exit rates, per time unit, in phase order) or ``durations`` (fixed phase durations), as PhysiCell does.

| Field | Type | Default |
|---|---|---|
| `model` | `live` \| `flow_cytometry` \| `flow_cytometry_separated` \| `ki67_basic` \| `ki67_advanced` \| `cycling_quiescent` \| `custom` | `live` |
| `rates` | list of number (>= 0) or null | none |
| `durations` | list of number (> 0) or null | none |

## Death

Apoptosis and necrosis rates, and how long a dead cell remains before it is removed.

| Field | Type | Default |
|---|---|---|
| `apoptosis_rate` | number (>= 0) | `0.0` |
| `necrosis_rate` | number (>= 0) | `0.0` |
| `apoptosis_duration` | number (> 0) | `516.0` |
| `necrosis_duration` | number (> 0) | `86400.0` |

## Mechanics

Centre-based mechanics. ``max_adhesion_distance`` is relative to the cell radius, as in PhysiCell.

| Field | Type | Default |
|---|---|---|
| `adhesion_strength` | number (>= 0) | `0.4` |
| `repulsion_strength` | number (>= 0) | `10.0` |
| `max_adhesion_distance` | number (>= 1.0) | `1.25` |
| `adhesion_affinities` | map of number (>= 0) | empty |

## Motility

Cell migration, as in PhysiCell: speed, persistence time and migration bias. ``migration_bias`` runs from 0 (a random direction) to 1 (the chemotaxis direction only).

| Field | Type | Default |
|---|---|---|
| `speed` | number (>= 0) | `0.0` |
| `persistence_time` | number (> 0) | `1.0` |
| `migration_bias` | number (>= 0, <= 1) | `0.0` |
| `chemotaxis` | [Chemotaxis](#chemotaxis) or null | none |

## Chemotaxis

Migration bias along one substrate's gradient: ``direction`` 1 moves up the gradient, -1 down it.

| Field | Type | Default |
|---|---|---|
| `substrate` | string (non-empty) | required |
| `direction` | `1` \| `-1` | `1` |

## Secretion

Exchange of one substrate between a cell and its voxel, as in PhysiCell. The cell secretes towards the ``target`` density at ``secretion_rate``, takes up at ``uptake_rate``, and adds ``net_export_rate`` of substrate per time unit.

| Field | Type | Default |
|---|---|---|
| `substrate` | string (non-empty) | required |
| `secretion_rate` | number (>= 0) | `0.0` |
| `target` | number (>= 0) | `1.0` |
| `uptake_rate` | number (>= 0) | `0.0` |
| `net_export_rate` | number | `0.0` |

## Interactions

Contact interactions with other cells, as in PhysiCell. Attack, phagocytosis and fusion rates are keyed by the name of the target cell type. ``damage_rate`` is the attack damage rate, and ``dead_phagocytosis_rate`` applies to dead cells of any type.

| Field | Type | Default |
|---|---|---|
| `attack_rates` | map of number (>= 0) | empty |
| `damage_rate` | number (>= 0) | `0.0` |
| `phagocytosis_rates` | map of number (>= 0) | empty |
| `dead_phagocytosis_rate` | number (>= 0) | `0.0` |
| `fusion_rates` | map of number (>= 0) | empty |

## Rule

One signal-to-behaviour rule in PhysiCell's cell behaviour grammar (CBHG, rules CSV version 3). ``saturation_value`` is the behaviour value when the signal saturates: the maximum for an ``increases`` rule and the minimum for a ``decreases`` rule. The base value comes from the cell type.

| Field | Type | Default |
|---|---|---|
| `cell_type` | string (non-empty) | required |
| `signal` | string (non-empty) | required |
| `direction` | `increases` \| `decreases` | required |
| `behaviour` | string (non-empty) | required |
| `saturation_value` | number (>= 0) | required |
| `half_max` | number (> 0) | required |
| `hill_power` | number (> 0) | required |
| `apply_to_dead` | boolean | `False` |

## IntracellularModel

A reference to an intracellular model file. Paths are relative to the IR file.

| Field | Type | Default |
|---|---|---|
| `cell_type` | string (non-empty) | required |
| `format` | `maboss` \| `sbml` \| `sbml-qual` | required |
| `files` | map of string | required |
| `inputs` | map of string | empty |
| `outputs` | map of string | empty |
| `time_step` | number (> 0) or null | none |
| `settings` | object | empty |

## InitialConditions

The cells present at the start of a run, as a list of placements.

| Field | Type | Default |
|---|---|---|
| `placements` | list of [Placement](#placement) | empty list |

## Placement

Initial cells. ``density`` is cells per unit area (2D) or volume (3D); ``positions`` are explicit.

| Field | Type | Default |
|---|---|---|
| `kind` | `disc` \| `rectangle` \| `random` \| `positions` \| `positions_csv` | required |
| `cell_type` | string (non-empty) or null | none |
| `count` | integer (>= 0) or null | none |
| `density` | number (>= 0) or null | none |
| `centre` | (number, number) | `[0.0, 0.0]` |
| `radius` | number (> 0) or null | none |
| `size` | (number (> 0), number (> 0)) or null | none |
| `positions` | list of (number, number) or null | none |
| `path` | string or null | none |

## TimedEvent

A change at a set time, held until ``until`` when given (then reverted), for example a drug pulse.

| Field | Type | Default |
|---|---|---|
| `time` | number (>= 0) | required |
| `action` | `set_substrate_boundary` \| `set_substrate` \| `add_cells` \| `set_parameter` | required |
| `target` | string (non-empty) | required |
| `value` | number or null | none |
| `until` | number (>= 0) or null | none |

## Observable

A named quantity measured at every saved time, such as a cell count or a substrate mean. ``cell_type`` restricts a count or position to one type. The ``substrate_`` kinds need ``substrate``, and ``type_fraction`` needs ``cell_type``.

| Field | Type | Default |
|---|---|---|
| `name` | string | required |
| `kind` | `cell_count` \| `live_count` \| `dead_count` \| `type_fraction` \| `mean_position` \| `radius_of_gyration` \| `substrate_mean` \| `substrate_min` \| `substrate_max` | required |
| `cell_type` | string (non-empty) or null | none |
| `substrate` | string (non-empty) or null | none |
| `axis` | `x` \| `y` \| `z` | `x` |

## Experiment

The run length, the time steps and the save interval, in the model's time unit, as in PhysiCell. ``dt_diffusion``, ``dt_mechanics`` and ``dt_phenotype`` are the steps of each process. ``threads`` is the number of threads an engine may use.

| Field | Type | Default |
|---|---|---|
| `max_time` | number (> 0) | required |
| `dt_diffusion` | number (> 0) | `0.01` |
| `dt_mechanics` | number (> 0) | `0.1` |
| `dt_phenotype` | number (> 0) | `6.0` |
| `save_interval` | number (> 0) | `60.0` |
| `threads` | integer (>= 1) | `1` |

## Provenance

Where a model came from: its source, DOI, authors, licence, origin project and the tool that made it.

| Field | Type | Default |
|---|---|---|
| `source` | string or null | none |
| `doi` | string or null | none |
| `authors` | list of string | empty list |
| `licence` | string or null | none |
| `derived_from` | [DerivedFrom](#derivedfrom) or null | none |
| `created_with` | string or null | none |
| `notes` | string | empty |

## DerivedFrom

The engine project a model was imported from, with an optional SHA-256 digest of its source.

| Field | Type | Default |
|---|---|---|
| `engine` | string | required |
| `path` | string | required |
| `sha256` | string or null | none |

## NativeExtension

Engine-specific code that the IR does not describe and never translates. ``affects`` names what it may change.

| Field | Type | Default |
|---|---|---|
| `engine` | string | required |
| `kind` | `custom_cpp` \| `custom_function` \| `other` | required |
| `files` | list of string | empty list |
| `description` | string | empty |
| `affects` | list of string | empty list |
