"""Verify the published files, native models and maintained source records."""
from pathlib import Path
import hashlib
import json

ROOT = Path(__file__).resolve().parent


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    manifest = json.loads((ROOT / "artifact_manifest.json").read_text())
    for name, record in manifest["files"].items():
        path = (ROOT / name).resolve()
        path.relative_to(ROOT)
        if digest(path) != record["sha256"]:
            raise ValueError("Artifact mismatch: " + name)
    source = json.loads((ROOT / "provenance/source_manifest.json").read_text())
    for name, record in source.items():
        if digest(ROOT / name) != record["sha256"]:
            raise ValueError("Source mismatch: " + name)
    metadata = json.loads((ROOT / "models/default/metadata.json").read_text())
    expected = {(c, q) for c in ("Solar", "Temperature") for q in (.16, .5, .84)}
    found = {(entry["channel"], entry["quantile"]) for entry in metadata["models"]}
    if len(metadata["models"]) != 6 or found != expected:
        raise ValueError("Incomplete native model bundle")
    for entry in metadata["models"]:
        path = (ROOT / "models/default" / entry["file"]).resolve()
        path.relative_to((ROOT / "models/default").resolve())
        if digest(path) != entry["sha256"]:
            raise ValueError("Native model mismatch: " + entry["file"])
    print(json.dumps({"status": "passed", "files_verified": len(manifest["files"]),
                      "native_models_verified": 6, "strict_temporal_holdout": False}, indent=2))


if __name__ == "__main__":
    main()
