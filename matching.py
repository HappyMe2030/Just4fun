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


def find_matching_printers(project, user_lat, user_lng, radius_km=200):
    """Printers that meet a project's required characteristics, within
    radius_km of the given (user_lat, user_lng), closest first."""
    sql = f"""
        SELECT * FROM (
            SELECT
                pr.*,
                {HAVERSINE_KM} AS distance_km,
                pe.name AS owner_name
            FROM printers pr
            JOIN people pe ON pe.id = pr.owner_id
            WHERE pr.technology = %(technology)s
              AND (
                    cardinality(%(materials)s::text[]) = 0
                    OR pr.materials && %(materials)s::text[]
                  )
              AND pr.build_volume_x_mm >= %(bx)s
              AND pr.build_volume_y_mm >= %(by)s
              AND pr.build_volume_z_mm >= %(bz)s
              AND (
                    %(max_nozzle)s IS NULL
                    OR pr.nozzle_diameter_mm IS NULL
                    OR pr.nozzle_diameter_mm <= %(max_nozzle)s
                  )
              AND (NOT %(need_bed)s OR pr.heated_bed)
              AND (NOT %(need_enclosed)s OR pr.enclosed)
        ) matched
        WHERE distance_km <= %(radius)s
        ORDER BY distance_km ASC
    """
    params = {
        "user_lat": user_lat,
        "user_lng": user_lng,
        "technology": project["required_technology"],
        "materials": project["required_materials"],
        "bx": project["required_build_volume_x_mm"],
        "by": project["required_build_volume_y_mm"],
        "bz": project["required_build_volume_z_mm"],
        "max_nozzle": project["required_nozzle_diameter_max_mm"],
        "need_bed": project["required_heated_bed"],
        "need_enclosed": project["required_enclosed"],
        "radius": radius_km,
    }
    with get_conn() as conn:
        with dict_cursor(conn) as cur:
            cur.execute(sql, params)
            return cur.fetchall()
