"""
A curated list of currently popular 3D printer models, used to let someone
adding a printer pick their model and auto-fill its known specs instead of
typing them by hand. Sourced from manufacturer/reviewer specs as of the
site's last update — someone can always adjust fields after picking a model,
since exact configs vary (upgrades, regional nozzle swaps, etc).

Each entry maps directly onto the printer form's fields. nozzle_diameter_mm
and max_nozzle_temp_c are None for resin printers (not applicable).
"""

PRINTER_CATALOG = [
    # --- Bambu Lab (FDM) ---
    {
        "brand": "Bambu Lab", "model": "A1 Mini",
        "technology": "FDM", "materials": ["PLA", "PETG", "TPU"],
        "nozzle_diameter_mm": 0.4, "build_volume_x_mm": 180, "build_volume_y_mm": 180,
        "build_volume_z_mm": 180, "heated_bed": True, "enclosed": False, "max_nozzle_temp_c": 300,
    },
    {
        "brand": "Bambu Lab", "model": "A1",
        "technology": "FDM", "materials": ["PLA", "PETG", "TPU"],
        "nozzle_diameter_mm": 0.4, "build_volume_x_mm": 256, "build_volume_y_mm": 256,
        "build_volume_z_mm": 256, "heated_bed": True, "enclosed": False, "max_nozzle_temp_c": 300,
    },
    {
        "brand": "Bambu Lab", "model": "P1S",
        "technology": "FDM", "materials": ["PLA", "PETG", "ABS", "ASA", "TPU"],
        "nozzle_diameter_mm": 0.4, "build_volume_x_mm": 256, "build_volume_y_mm": 256,
        "build_volume_z_mm": 256, "heated_bed": True, "enclosed": True, "max_nozzle_temp_c": 300,
    },
    {
        "brand": "Bambu Lab", "model": "P2S",
        "technology": "FDM", "materials": ["PLA", "PETG", "ABS", "ASA", "NYLON", "PC"],
        "nozzle_diameter_mm": 0.4, "build_volume_x_mm": 256, "build_volume_y_mm": 256,
        "build_volume_z_mm": 256, "heated_bed": True, "enclosed": True, "max_nozzle_temp_c": 320,
    },
    {
        "brand": "Bambu Lab", "model": "X2D",
        "technology": "FDM", "materials": ["PLA", "PETG", "ABS", "ASA", "NYLON", "PC"],
        "nozzle_diameter_mm": 0.4, "build_volume_x_mm": 256, "build_volume_y_mm": 256,
        "build_volume_z_mm": 260, "heated_bed": True, "enclosed": True, "max_nozzle_temp_c": 320,
    },

    # --- Prusa (FDM) ---
    {
        "brand": "Prusa", "model": "MINI+",
        "technology": "FDM", "materials": ["PLA", "PETG", "ASA"],
        "nozzle_diameter_mm": 0.4, "build_volume_x_mm": 180, "build_volume_y_mm": 180,
        "build_volume_z_mm": 180, "heated_bed": True, "enclosed": False, "max_nozzle_temp_c": 280,
    },
    {
        "brand": "Prusa", "model": "MK4S",
        "technology": "FDM", "materials": ["PLA", "PETG", "ASA", "ABS", "TPU"],
        "nozzle_diameter_mm": 0.4, "build_volume_x_mm": 250, "build_volume_y_mm": 210,
        "build_volume_z_mm": 220, "heated_bed": True, "enclosed": False, "max_nozzle_temp_c": 290,
    },
    {
        "brand": "Prusa", "model": "CORE One",
        "technology": "FDM", "materials": ["PLA", "PETG", "ABS", "ASA", "PC", "NYLON"],
        "nozzle_diameter_mm": 0.4, "build_volume_x_mm": 250, "build_volume_y_mm": 220,
        "build_volume_z_mm": 270, "heated_bed": True, "enclosed": True, "max_nozzle_temp_c": 290,
    },

    # --- Creality (FDM) ---
    {
        "brand": "Creality", "model": "Ender 3 V3 SE",
        "technology": "FDM", "materials": ["PLA", "PETG", "TPU"],
        "nozzle_diameter_mm": 0.4, "build_volume_x_mm": 220, "build_volume_y_mm": 220,
        "build_volume_z_mm": 250, "heated_bed": True, "enclosed": False, "max_nozzle_temp_c": 260,
    },
    {
        "brand": "Creality", "model": "K1",
        "technology": "FDM", "materials": ["PLA", "PETG", "ABS", "TPU"],
        "nozzle_diameter_mm": 0.4, "build_volume_x_mm": 220, "build_volume_y_mm": 220,
        "build_volume_z_mm": 250, "heated_bed": True, "enclosed": True, "max_nozzle_temp_c": 300,
    },

    # --- Anycubic (FDM) ---
    {
        "brand": "Anycubic", "model": "Kobra 3",
        "technology": "FDM", "materials": ["PLA", "PETG", "TPU"],
        "nozzle_diameter_mm": 0.4, "build_volume_x_mm": 250, "build_volume_y_mm": 250,
        "build_volume_z_mm": 260, "heated_bed": True, "enclosed": False, "max_nozzle_temp_c": 260,
    },

    # --- Resin / MSLA printers ---
    {
        "brand": "Elegoo", "model": "Mars 5",
        "technology": "SLA", "materials": ["RESIN_STANDARD"],
        "nozzle_diameter_mm": None, "build_volume_x_mm": 153, "build_volume_y_mm": 77,
        "build_volume_z_mm": 165, "heated_bed": False, "enclosed": True, "max_nozzle_temp_c": None,
    },
    {
        "brand": "Elegoo", "model": "Saturn 4 Ultra",
        "technology": "SLA", "materials": ["RESIN_STANDARD", "RESIN_TOUGH"],
        "nozzle_diameter_mm": None, "build_volume_x_mm": 218, "build_volume_y_mm": 123,
        "build_volume_z_mm": 220, "heated_bed": False, "enclosed": True, "max_nozzle_temp_c": None,
    },
    {
        "brand": "Anycubic", "model": "Photon Mono M7 Pro",
        "technology": "SLA", "materials": ["RESIN_STANDARD", "RESIN_TOUGH", "RESIN_FLEXIBLE"],
        "nozzle_diameter_mm": None, "build_volume_x_mm": 198, "build_volume_y_mm": 122,
        "build_volume_z_mm": 200, "heated_bed": False, "enclosed": True, "max_nozzle_temp_c": None,
    },
    {
        "brand": "Anycubic", "model": "Photon Mono M7 Max",
        "technology": "SLA", "materials": ["RESIN_STANDARD", "RESIN_TOUGH"],
        "nozzle_diameter_mm": None, "build_volume_x_mm": 298, "build_volume_y_mm": 164,
        "build_volume_z_mm": 300, "heated_bed": False, "enclosed": True, "max_nozzle_temp_c": None,
    },
]
