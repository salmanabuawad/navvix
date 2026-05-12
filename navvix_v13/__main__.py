"""
navvix_v13 CLI
--------------
Usage:

  # Learn from training samples
  python -m navvix_v13 train --samples path/to/samples/ --model style_model.json

  # Apply learned model to a new DXF
  python -m navvix_v13 apply --input floor.dxf --model style_model.json --output-dir out/

  # One-shot: train then apply
  python -m navvix_v13 full --samples samples/ --input floor.dxf --output-dir out/
"""

from __future__ import annotations

import argparse, json, sys
from pathlib import Path

# ── shared helpers from v12 ──────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent.parent))
from navvix_v12.__main__ import isolate, preview as gen_preview

from navvix_v13.learner import learn
from navvix_v13.applier import apply as apply_model


def _resolve_paths(args: list[str]) -> list[Path]:
    paths: list[Path] = []
    for a in args:
        p = Path(a)
        if p.is_dir():
            paths.extend(p.glob("**/*.dxf"))
            paths.extend(p.glob("**/*.dwfx"))
        elif p.is_file():
            paths.append(p)
    return paths


def cmd_train(args):
    sample_paths = _resolve_paths(args.samples)
    if not sample_paths:
        print("ERROR: no .dxf/.dwfx files found in samples", file=sys.stderr)
        sys.exit(1)
    print(f"[train] {len(sample_paths)} training file(s) found…")
    model, report = learn(sample_paths, args.model)
    print(json.dumps(report, indent=2, default=str))


def cmd_apply(args):
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    iso = out / "isolated_main.dxf"
    dim = out / "dimensioned.dxf"

    print("[1/3] Isolating main plan…")
    iso_report = isolate(args.input, iso)

    print("[2/3] Applying learned model…")
    gen_report = apply_model(iso, dim, args.model)

    print("[3/3] Rendering preview…")
    gen_preview(dim, out / "preview.png", out / "preview.pdf",
                tuple(gen_report["bbox"]))

    report = {
        "input":           args.input,
        "model":           args.model,
        "isolated_dxf":    str(iso),
        "dimensioned_dxf": str(dim),
        "preview_png":     str(out / "preview.png"),
        "preview_pdf":     str(out / "preview.pdf"),
        "isolation":       iso_report,
        "generation":      gen_report,
    }
    (out / "report.json").write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8"
    )
    print(json.dumps({
        "status":    "ok",
        "dims":      len(gen_report["dimensions"]),
        "internal":  gen_report["internal_count"],
        "model":     gen_report.get("model_version", "v13"),
    }, indent=2))


def cmd_full(args):
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    model_path = out / "style_model.json"

    sample_paths = _resolve_paths(args.samples)
    print(f"[train] {len(sample_paths)} training file(s)…")
    _, train_report = learn(sample_paths, model_path)

    iso = out / "isolated_main.dxf"
    dim = out / "dimensioned.dxf"
    print("[isolate] extracting main plan…")
    iso_report = isolate(args.input, iso)
    print("[apply] dimensioning with learned model…")
    gen_report = apply_model(iso, dim, model_path)
    gen_preview(dim, out / "preview.png", out / "preview.pdf",
                tuple(gen_report["bbox"]))

    report = {
        "training":   train_report,
        "isolation":  iso_report,
        "generation": gen_report,
    }
    (out / "report.json").write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8"
    )
    print(json.dumps({
        "status":       "ok",
        "trained_on":   train_report["files_processed"],
        "dims_created": len(gen_report["dimensions"]),
    }, indent=2))


def main():
    ap  = argparse.ArgumentParser(description="navvix_v13 — learned DXF dimensioner")
    sub = ap.add_subparsers(dest="cmd", required=True)

    tr = sub.add_parser("train", help="Learn from training DXFs")
    tr.add_argument("--samples", nargs="+", required=True)
    tr.add_argument("--model",   required=True)

    ap2 = sub.add_parser("apply", help="Apply model to a new DXF")
    ap2.add_argument("--input",      required=True)
    ap2.add_argument("--model",      required=True)
    ap2.add_argument("--output-dir", required=True)

    fu = sub.add_parser("full", help="Train then apply in one step")
    fu.add_argument("--samples",    nargs="+", required=True)
    fu.add_argument("--input",      required=True)
    fu.add_argument("--output-dir", required=True)

    args = ap.parse_args()
    {"train": cmd_train, "apply": cmd_apply, "full": cmd_full}[args.cmd](args)


if __name__ == "__main__":
    main()
