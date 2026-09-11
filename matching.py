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
