# ABM-IR 0.1 reference

Generated from the pydantic models by `scripts/make_ir_reference.py`; the JSON Schema is
[`schemas/abm-ir-0.1.json`](../schemas/abm-ir-0.1.json). Units follow the
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

| Field | Type | Default |
|---|---|---|
| `space` | string | `micron` |
| `time` | string | `min` |

## Space

| Field | Type | Default |
|---|---|---|
| `dimensions` | `2` \| `3` | `2` |
| `bounds` | [Bounds](#bounds) | required |
| `voxel` | [Voxel](#voxel) | required |
| `boundary` | `closed` \| `periodic` | `closed` |

## Bounds

| Field | Type | Default |
|---|---|---|
| `x_min` | number | required |
| `x_max` | number | required |
| `y_min` | number | required |
| `y_max` | number | required |
| `z_min` | number | `-10.0` |
| `z_max` | number | `10.0` |

## Voxel

| Field | Type | Default |
|---|---|---|
| `dx` | number (> 0) | required |
| `dy` | number (> 0) | required |
| `dz` | number (> 0) | `20.0` |

## Substrate

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

| Field | Type | Default |
|---|---|---|
| `speed` | number (>= 0) | `0.0` |
| `persistence_time` | number (> 0) | `1.0` |
| `migration_bias` | number (>= 0, <= 1) | `0.0` |
| `chemotaxis` | [Chemotaxis](#chemotaxis) or null | none |

## Chemotaxis

| Field | Type | Default |
|---|---|---|
| `substrate` | string (non-empty) | required |
| `direction` | `1` \| `-1` | `1` |

## Secretion

| Field | Type | Default |
|---|---|---|
| `substrate` | string (non-empty) | required |
| `secretion_rate` | number (>= 0) | `0.0` |
| `target` | number (>= 0) | `1.0` |
| `uptake_rate` | number (>= 0) | `0.0` |
| `net_export_rate` | number | `0.0` |

## Interactions

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

| Field | Type | Default |
|---|---|---|
| `name` | string | required |
| `kind` | `cell_count` \| `live_count` \| `dead_count` \| `type_fraction` \| `mean_position` \| `radius_of_gyration` \| `substrate_mean` \| `substrate_min` \| `substrate_max` | required |
| `cell_type` | string (non-empty) or null | none |
| `substrate` | string (non-empty) or null | none |
| `axis` | `x` \| `y` \| `z` | `x` |

## Experiment

| Field | Type | Default |
|---|---|---|
| `max_time` | number (> 0) | required |
| `dt_diffusion` | number (> 0) | `0.01` |
| `dt_mechanics` | number (> 0) | `0.1` |
| `dt_phenotype` | number (> 0) | `6.0` |
| `save_interval` | number (> 0) | `60.0` |
| `threads` | integer (>= 1) | `1` |

## Provenance

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

| Field | Type | Default |
|---|---|---|
| `engine` | string | required |
| `path` | string | required |
| `sha256` | string or null | none |

## NativeExtension

| Field | Type | Default |
|---|---|---|
| `engine` | string | required |
| `kind` | `custom_cpp` \| `custom_function` \| `other` | required |
| `files` | list of string | empty list |
| `description` | string | empty |
| `affects` | list of string | empty list |
