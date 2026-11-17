"""Shared settings for the pipeline scripts."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# Where downloaded and generated data lives. Set ATLAS_DATA_DIR in .env to keep big files
# out of OneDrive, e.g.  ATLAS_DATA_DIR=C:\Users\schil\atlas-data
DATA = Path(os.getenv("ATLAS_DATA_DIR", ROOT / "data"))

# Metre-based map projection for distances and areas (UTM zone 18N, covers Northern Virginia).
METRIC_CRS = 26918

# The three submarkets Larkspur Commercial Partners covers (west, south, east, north in lon/lat).
SUBMARKETS = {
    "tysons": (-77.270, 38.890, -77.185, 38.950),
    "reston_herndon": (-77.425, 38.930, -77.300, 38.980),
    "richmond_hwy": (-77.150, 38.715, -77.050, 38.800),
}
