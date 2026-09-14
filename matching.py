from db import get_conn, dict_cursor

HAVERSINE_KM = """
        6371 * acos(
            LEAST(1.0, GREATEST(-1.0,
                cos(radians(%(user_lat)s)) * cos(radians(pr.latitude)) *
                cos(radians(pr.longitude) - radians(%(user_lng)s)) +
                sin(radians(%(user_lat)s)) * sin(radians(pr.latitude))
            ))
        )
"""


def _mismatch_reasons(printer, project):
    """List of human-readable reasons a printer doesn't meet a project's
    requirements. Empty list means it's a full match."""
    reasons = []

    if printer["technology"] != project["required_technology"]:
        reasons.append(
            f"Wrong technology: printer is {printer['technology']}, "
            f"project needs {project['required_technology']}"
        )

    required_materials = project["required_materials"] or []
    if required_materials:
        printer_materials = set(printer["materials"] or [])
        if not printer_materials.intersection(required_materials):
            reasons.append(
                "No matching material: printer supports "
                f"{', '.join(printer['materials']) or 'none listed'}, "
                f"project needs one of {', '.join(required_materials)}"
            )

    for axis, label in (("x", "X"), ("y", "Y"), ("z", "Z")):
        needed = project[f"required_build_volume_{axis}_mm"]
        have = printer[f"build_volume_{axis}_mm"]
        if have < needed:
            reasons.append(
                f"Build volume too small on {label}: printer has {have}mm, "
                f"project needs at least {needed}mm"
            )

    max_nozzle = project["required_nozzle_diameter_max_mm"]
    if max_nozzle is not None:
        if printer["nozzle_diameter_mm"] is None:
            reasons.append("Printer has no nozzle diameter on file to check detail level")
        elif printer["nozzle_diameter_mm"] > max_nozzle:
            reasons.append(
                f"Nozzle too coarse: printer is {printer['nozzle_diameter_mm']}mm, "
                f"project needs {max_nozzle}mm or finer"
            )

    if project["required_heated_bed"] and not printer["heated_bed"]:
        reasons.append("Project needs a heated bed, printer doesn't have one")

    if project["required_enclosed"] and not printer["enclosed"]:
        reasons.append("Project needs an enclosed chamber, printer doesn't have one")

    return reasons


def find_nearby_printers(project, user_lat, user_lng, radius_km=200):
    """All printers within radius_km of (user_lat, user_lng), closest first,
    each annotated with distance_km and mismatch_reasons (empty = full match)."""
    sql = f"""
        SELECT pr.*, {HAVERSINE_KM} AS distance_km, pe.name AS owner_name
        FROM printers pr
        JOIN people pe ON pe.id = pr.owner_id
        WHERE {HAVERSINE_KM} <= %(radius)s
        ORDER BY distance_km ASC
    """
    params = {
        "user_lat": user_lat, "user_lng": user_lng, "radius": radius_km,
    }
    with get_conn() as conn:
        with dict_cursor(conn) as cur:
            cur.execute(sql, params)
            printers = cur.fetchall()

    for printer in printers:
        printer["mismatch_reasons"] = _mismatch_reasons(printer, project)

    return printers


def browse_printers(user_lat, user_lng, page=1, per_page=10):
    """All printers, nearest first, with average rating/review count and a
    few recent review comments each. Returns (printers, total_count)."""
    offset = (page - 1) * per_page

    with get_conn() as conn:
        with dict_cursor(conn) as cur:
            cur.execute("SELECT COUNT(*) AS cnt FROM printers")
            total = cur.fetchone()["cnt"]

            cur.execute(
                f"""
                SELECT pr.*, {HAVERSINE_KM} AS distance_km, pe.name AS owner_name,
                       COALESCE(rv.avg_rating, 0) AS avg_rating,
                       COALESCE(rv.review_count, 0) AS review_count
                FROM printers pr
                JOIN people pe ON pe.id = pr.owner_id
                LEFT JOIN (
                    SELECT target_id, AVG(rating)::numeric(3,2) AS avg_rating,
                           COUNT(*) AS review_count
                    FROM reviews WHERE target_type = 'printer' GROUP BY target_id
                ) rv ON rv.target_id = pr.id
                ORDER BY distance_km ASC
                LIMIT %(limit)s OFFSET %(offset)s
                """,
                {
                    "user_lat": user_lat, "user_lng": user_lng,
                    "limit": per_page, "offset": offset,
                },
            )
            printers = cur.fetchall()

            printer_ids = [p["id"] for p in printers]
            comments_by_printer = {pid: [] for pid in printer_ids}
            if printer_ids:
                cur.execute(
                    """
                    SELECT rv.target_id AS printer_id, rv.rating, rv.comment, rv.created_at,
                           pe.name AS reviewer_name
                    FROM reviews rv
                    JOIN people pe ON pe.id = rv.rater_id
                    WHERE rv.target_type = 'printer' AND rv.target_id = ANY(%s)
                      AND rv.comment IS NOT NULL AND rv.comment != ''
                    ORDER BY rv.created_at DESC
                    """,
                    (printer_ids,),
                )
                for row in cur.fetchall():
                    bucket = comments_by_printer[row["printer_id"]]
                    if len(bucket) < 3:
                        bucket.append(row)

    for printer in printers:
        printer["recent_comments"] = comments_by_printer.get(printer["id"], [])

    return printers, total


def get_target_rating(target_type, target_id, max_comments=3):
    """Average rating/count plus a few recent comments for any review
    target: 'printer', 'project', or 'customer' (target_id = people.id)."""
    with get_conn() as conn:
        with dict_cursor(conn) as cur:
            cur.execute(
                """
                SELECT COALESCE(AVG(rating)::numeric(3,2), 0) AS avg_rating,
                       COUNT(*) AS review_count
                FROM reviews WHERE target_type = %s AND target_id = %s
                """,
                (target_type, target_id),
            )
            agg = cur.fetchone()

            cur.execute(
                """
                SELECT rv.rating, rv.comment, rv.created_at, pe.name AS reviewer_name
                FROM reviews rv
                JOIN people pe ON pe.id = rv.rater_id
                WHERE rv.target_type = %s AND rv.target_id = %s
                  AND rv.comment IS NOT NULL AND rv.comment != ''
                ORDER BY rv.created_at DESC
                LIMIT %s
                """,
                (target_type, target_id, max_comments),
            )
            comments = cur.fetchall()

    return {
        "avg_rating": agg["avg_rating"], "review_count": agg["review_count"],
        "comments": comments,
    }
