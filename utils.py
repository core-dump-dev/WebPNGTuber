import zipfile, os, json
def export_model_zip(model_json, model_dir):
    # create zip next to model_dir
    base = os.path.basename(model_dir.rstrip("/\\"))
    zippath = os.path.join(os.path.dirname(model_dir), base + ".zip")
    with zipfile.ZipFile(zippath, "w", compression=zipfile.ZIP_DEFLATED) as z:
        # add JSON
        z.writestr("model.json", json.dumps(model_json, indent=2, ensure_ascii=False))
        # add image files referenced
        for l in model_json.get("layers", []):
            f = l.get("file")
            if not f:
                continue
            fp = os.path.join(model_dir, f)
            if os.path.exists(fp):
                z.write(fp, arcname=f)
    return zippath