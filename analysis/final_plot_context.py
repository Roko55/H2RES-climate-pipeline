"""Current data binding for the retained chart design."""
from types import SimpleNamespace
from analysis.final_results import ROOT,YEARS,rows
raw={'rows':rows(ROOT)}
source=SimpleNamespace(YEARS=YEARS)
