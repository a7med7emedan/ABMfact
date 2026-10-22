# PhysiCell fixtures

Files copied unchanged from PhysiCell 1.14.2: from `sample_projects/template`, the files `main.cpp`,
`Makefile`, `custom_modules/` and `config/`; from `sample_projects/rules_sample`, the folder `config/`,
except `cells.csv`, which keeps only the first ten cells. PhysiCell is distributed under the BSD 3-Clause
licence; see `LICENSE-PhysiCell.txt`.

`output_tiny/` is real output from PhysiCell 1.14.2: the template project built in
`containers/physicell/Dockerfile`, on a 200 by 200 micron domain with four cells, seed 1, one thread,
60 minutes, saved every 60 minutes. Only the XML and MATLAB files the reader uses are kept.
