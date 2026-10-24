"""Fetch Overture Maps Places for Singapore and save them as a PlayMap Stage0 source.

Run on your own computer (needs internet access to Overture's public S3 bucket):

    pip install duckdb
    # step 1: see which categories exist in Singapore, and how many places each has
    python scripts/fetch_overture_places.py profile
    # step 2: list the sub-categories inside big top-level categories
    python scripts/fetch_overture_places.py profile --top food_and_drink shopping --out overture_sub.txt
    # step 3: export whole top-level categories and/or chosen sub-categories
    python scripts/fetch_overture_places.py export --include arts_and_entertainment --include-sub shopping_mall food_court

Output (same layout the Stage1 build already reads):
    data/raw/overture_places/overture_places_<release>.geojson
    data/manifests/overture_places.json

Then run scripts/add_overture_to_catalogue.py. Do NOT add overture_places to
config/sources.json: Stage1C checks that registry against Stage1B.

Notes
- Overture is a large merged dataset (Meta, Microsoft, Foursquare, etc.). Records are
  candidates, not verified destinations. Opening hours are NOT in Overture.
- Singapore bbox also touches Johor and Batam, so we keep only addresses with country 'SG'.
- Overture place ids (GERS ids) are stable across releases, so they are used as OBJECTID.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import urllib.request
from pathlib import Path

SG_BBOX = (103.59, 1.15, 104.10, 1.48)  # xmin, ymin, xmax, ymax
STAC_URL = "https://stac.overturemaps.org/catalog.json"
S3_TEMPLATE = "s3://overturemaps-us-west-2/release/{release}/theme=places/type=place/*"
SOURCE_KEY = "overture_places"
ROOT = Path(__file__).resolve().parent.parent

SOURCE_ENTRY = {
    "key": SOURCE_KEY,
    "title": "Overture Maps Places",
    "agency": "Overture Maps Foundation",
    "dataset_id": SOURCE_KEY,
    "catalogue_url": "https://docs.overturemaps.org/getting-data/",
    "poll_url": None,
    "format": "geojson",
    "role": "candidate_places",
    "geographic_scope": "Singapore bbox, filtered to address country SG",
    "download_policy": "category_filtered_by_project_team",
    "source_period_label": "see manifest (Overture release id)",
    "reviewed_on": None,
    "review_level": "schema_reviewed_records_not_verified",
    "runtime_download_verified": False,
    "expected_geometry_types": ["Point"],
    "purpose": "补充官方数据没有覆盖的娱乐、体验、商场等地点；作为候选点，需要核对。",
    "cautions": [
        "多来源合并数据，存在重复、过时或位置偏差",
        "没有营业时间；operating_status 只表示是否仍在营业",
        "confidence 是 Overture 的存在可信度，不是质量评分",
        "需按 Overture 要求注明数据来源",
    ],
    "licence_name": "See Overture attribution page (Places: CDLA-Permissive-2.0)",
    "licence_url": "https://docs.overturemaps.org/attribution",
}


def latest_release() -> str:
    with urllib.request.urlopen(STAC_URL, timeout=30) as r:
        return json.load(r)["latest"]


def connect(remote: bool = True):
    try:
        import duckdb
    except ImportError:
        sys.exit("Please run: pip install duckdb")
    con = duckdb.connect()
    if remote:
        con.execute("INSTALL httpfs; LOAD httpfs; SET s3_region='us-west-2';")
    return con


def base_query(source: str) -> str:
    xmin, ymin, xmax, ymax = SG_BBOX
    return f"""
        SELECT
            id,
            names.primary                     AS name,
            basic_category,
            taxonomy.primary                  AS category,
            taxonomy.hierarchy                AS hierarchy,
            CAST(confidence AS DOUBLE)        AS confidence,
            operating_status,
            websites[1]                       AS website,
            phones[1]                         AS phone,
            addresses[1].freeform             AS address,
            addresses[1].postcode             AS postcode,
            addresses[1].country              AS country,
            brand.names.primary               AS brand,
            CAST((bbox.xmin + bbox.xmax) / 2 AS DOUBLE) AS lon,
            CAST((bbox.ymin + bbox.ymax) / 2 AS DOUBLE) AS lat,
            list_distinct([s.dataset FOR s IN sources]) AS contributing_datasets
        FROM read_parquet('{source}', hive_partitioning=1)
        WHERE bbox.xmin BETWEEN {xmin} AND {xmax}
          AND bbox.ymin BETWEEN {ymin} AND {ymax}
          AND addresses[1].country = 'SG'
    """


def cmd_profile_sub(args, con, source):
    tops = [s.strip() for s in args.top]
    ph = ", ".join("?" for _ in tops)
    print(f"Release: {args.release}\nSub-categories of {', '.join(tops)} "
          f"(open, confidence >= {args.min_confidence}) (1-3 min)...")
    rows = con.execute(f"""
        WITH p AS ({base_query(source)})
        SELECT hierarchy[1] AS top_level, coalesce(category, '(none)') AS category, count(*) AS n,
               string_agg(name, ' | ' ORDER BY confidence DESC)[1:80] AS examples
        FROM p
        WHERE coalesce(operating_status, 'open') = 'open'
          AND coalesce(confidence, 0) >= ?
          AND hierarchy[1] IN ({ph})
          AND name IS NOT NULL
        GROUP BY 1, 2 ORDER BY 1, n DESC
    """, [args.min_confidence, *tops]).fetchall()
    print(f"\n{'top-level':<24}{'sub-category':<36}{'count':>7}   examples")
    for top, cat, n, ex in rows:
        print(f"{top:<24}{cat:<36}{n:>7}   {ex or ''}")
    print(f"\nTotal: {sum(r[2] for r in rows)}")
    print("Pick sub-category names above and pass them to: export --include-sub <name> <name> ...")


def cmd_profile(args):
    if args.out:
        import contextlib, io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            _profile(args)
        Path(args.out).write_text(buf.getvalue(), encoding="utf-8-sig")
        print(buf.getvalue())
        print(f"Saved to {args.out}")
    else:
        _profile(args)


def _profile(args):
    con = connect(remote=not args.source)
    source = args.source or S3_TEMPLATE.format(release=args.release)
    if args.top:
        return cmd_profile_sub(args, con, source)
    print(f"Release: {args.release}\nCounting Singapore places by top-level category (1-3 min)...")
    rows = con.execute(f"""
        WITH p AS ({base_query(source)})
        SELECT coalesce(hierarchy[1], '(none)') AS top_level, count(*) AS n,
               round(avg(confidence), 2) AS avg_conf,
               string_agg(DISTINCT category, ', ') AS examples
        FROM p
        WHERE coalesce(operating_status, 'open') = 'open'
        GROUP BY 1 ORDER BY n DESC
    """).fetchall()
    total = sum(r[1] for r in rows)
    print(f"\n{'top-level category':<34}{'count':>8}{'avg conf':>10}")
    for top, n, conf, ex in rows:
        print(f"{top:<34}{n:>8}{conf if conf is not None else '-':>10}   e.g. {(ex or '')[:70]}")
    print(f"\nTotal open places in SG: {total}")
    print("Pick top-level names above and pass them to: export --include <name> <name> ...")


def cmd_export(args):
    con = connect(remote=not args.source)
    source = args.source or S3_TEMPLATE.format(release=args.release)
    includes = [s.strip() for s in (args.include or [])]
    subs = [s.strip() for s in (args.include_sub or [])]
    if not includes and not subs:
        sys.exit("Give --include <top-level ...> and/or --include-sub <sub-category ...>")
    # A list with one impossible value keeps the SQL valid when one option is unused
    top_ph = ", ".join("?" for _ in includes) or "NULL"
    sub_ph = ", ".join("?" for _ in subs) or "NULL"
    sql = f"""
        WITH p AS ({base_query(source)})
        SELECT * FROM p
        WHERE coalesce(operating_status, 'open') = 'open'
          AND coalesce(confidence, 0) >= ?
          AND (hierarchy[1] IN ({top_ph}) OR category IN ({sub_ph}))
          AND name IS NOT NULL
        ORDER BY id
    """
    print(f"Release: {args.release}\nQuerying top-level: {', '.join(includes) or '-'}; "
          f"sub-categories: {', '.join(subs) or '-'} (min confidence {args.min_confidence})...")
    cur = con.execute(sql, [args.min_confidence, *includes, *subs])
    cols = [d[0] for d in cur.description]
    features = []
    for row in cur.fetchall():
        r = dict(zip(cols, row))
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(r["lon"], 7), round(r["lat"], 7)]},
            "properties": {
                # Field names already read by app/stage1/normalise.py
                "OBJECTID": r["id"],
                "NAME": r["name"],
                "ADDRESS": " ".join(v for v in (r["address"], r["postcode"]) if v) or None,
                "HYPERLINK": r["website"],
                # Extra Overture fields, kept for review
                "OVERTURE_TOP_LEVEL": r["hierarchy"][0] if r["hierarchy"] else None,
                "OVERTURE_CATEGORY": r["category"],
                "OVERTURE_HIERARCHY": " > ".join(r["hierarchy"] or []),
                "BASIC_CATEGORY": r["basic_category"],
                "CONFIDENCE": r["confidence"],
                "OPERATING_STATUS": r["operating_status"],
                "PHONE": r["phone"],
                "BRAND": r["brand"],
                "CONTRIBUTING_DATASETS": ", ".join(r["contributing_datasets"] or []),
            },
        })

    raw_rel = Path("data") / "raw" / SOURCE_KEY / f"{SOURCE_KEY}_{args.release}.geojson"
    raw = ROOT / raw_rel
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text(json.dumps({"type": "FeatureCollection", "features": features},
                              ensure_ascii=False), encoding="utf-8")

    manifest = {
        "source_key": SOURCE_KEY,
        "dataset_id": SOURCE_KEY,
        "relative_path": raw_rel.as_posix(),
        "bytes": raw.stat().st_size,
        "local_status": "downloaded",
        "retrieved_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "source_period_label": f"Overture release {args.release}",
        "filters": {"bbox": SG_BBOX, "country": "SG", "operating_status": "open",
                    "min_confidence": args.min_confidence, "top_level_categories": includes,
                    "sub_categories": subs},
        "summary": {"feature_count": len(features)},
        "source": SOURCE_ENTRY,
    }
    man = ROOT / "data" / "manifests" / f"{SOURCE_KEY}.json"
    man.parent.mkdir(parents=True, exist_ok=True)
    man.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\nSaved {len(features)} places")
    print(f"  {raw_rel.as_posix()}\n  data/manifests/{SOURCE_KEY}.json")
    print("\nNext step (adds these places to the app catalogue as a new snapshot):")
    print("  python scripts/add_overture_to_catalogue.py")


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except AttributeError:
            pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--release", help="Overture release id; default = latest from STAC")
    ap.add_argument("--source", help="(testing) local parquet path instead of S3")
    sub = ap.add_subparsers(dest="cmd", required=True)
    pr = sub.add_parser("profile", help="count SG places by top-level category, or by sub-category with --top")
    pr.add_argument("--top", nargs="+", help="list sub-categories inside these top-level categories")
    pr.add_argument("--min-confidence", type=float, default=0.75, help="used with --top")
    pr.add_argument("--out", help="also save the result to this UTF-8 text file")
    ex = sub.add_parser("export", help="export chosen categories as a PlayMap source")
    ex.add_argument("--include", nargs="+", help="whole top-level categories (from profile)")
    ex.add_argument("--include-sub", nargs="+", help="single sub-categories (from profile --top)")
    ex.add_argument("--min-confidence", type=float, default=0.75)
    args = ap.parse_args()
    if not args.release:
        args.release = "local-test" if args.source else latest_release()
    {"profile": cmd_profile, "export": cmd_export}[args.cmd](args)


if __name__ == "__main__":
    main()
