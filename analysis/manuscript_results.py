"""Reproduce manuscript tables and verify their source identities."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from analysis.final_results import summaries,main
from analysis.legacy_manuscript_results import verify_manuscript
if __name__=='__main__':main()
