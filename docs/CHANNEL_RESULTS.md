# Current channel cases

Average uses zero-based member 65 for each channel. Solar Low/High replace only
solar (2/28); Wind Low/High only wind (20/72); Cold/Warm only temperature (41/48).
Complete hourly profiles repeat across planning years without future weather trends.
The common 2020 base is followed by each path's own realized capacity history.

The current result cohort is evidence/final, with tables in results/final.
See RESEARCH_RESULTS.md and provenance/channel_sensitivity/design.json.

Export inputs with:
python pipeline.py select-final --design channel --year 2030 --output exports/cases.npz

Reproduce current results with:
python analysis/manuscript_results.py --output exports/current_results
